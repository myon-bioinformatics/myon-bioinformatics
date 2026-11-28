# markdown.py
# metadata: __all__=128 | base_sha=99b6a174a883f60a9c3ed01164a81fd7bd26ff76 | updated_at=2026-09-27T09:40:47Z
"""Stdlib-only Markdown utility functions.

This module is a single standard-library file; its optional CLI delegates to the same helpers.
It focuses on reusable helpers for Markdown I/O, structure extraction, and
conservative HTML <-> Markdown conversions (links, images, common tags).

It is **not** a full CommonMark implementation. Prefer the inventory helpers
and tests to see what is and is not handled.
"""

from __future__ import annotations

__all__ = [
    "save_markdown",
    "read_markdown",
    "run_markdown_doctest",
    "extract_sections",
    "split_sections",
    "split_slides",
    "extract_links",
    "extract_images",
    "extract_code_blocks",
    "extract_raw_html",
    "extract_urls",
    "extract_data_uris",
    "slugify",
    "table_of_contents",
    "extract_section",
    "strip_prose_keep_structure",
    "minify_markdown",
    "safe_truncate",
    "inventory",
    "make_link",
    "make_image",
    "html_image_to_markdown",
    "markdown_image_to_html",
    "html_link_to_markdown",
    "markdown_link_to_html",
    "html_to_markdown",
    "markdown_to_html",
    "markdown_to_web_ui_v1",
    "HtmlNode",
    "parse_html_dom",
    "html_text_content",
    "find_html_text",
    "dom_to_html",
    "dom_to_markdown",
    "markdown_to_dom",
    "markdown_to_kramdown",
    "kramdown_to_markdown",
    "ial",
    "with_attributes",
    "alert_stylesheet",
    "default_stylesheet",
    "is_probably_url",
    "heading",
    "bold",
    "italic",
    "strikethrough",
    "blockquote",
    "alert",
    "horizontal_rule",
    "bullet_list",
    "numbered_list",
    "task_item",
    "task_list",
    "details",
    "footnote_ref",
    "footnote",
    "code_block",
    "mermaid_block",
    "extract_mermaid_blocks",
    "markdown_headings_to_mermaid_mindmap",
    "markdown_tasks_to_mermaid_flowchart",
    "python_to_mermaid_class_diagram",
    "markdown_links_to_dot",
    "inspect_to_markdown",
    "argparse_to_markdown",
    "distribution_to_markdown",
    "calendar_to_markdown",
    "platform_to_markdown",
    "email_to_markdown",
    "markdown_to_email",
    "markdown_to_man",
    "markdown_to_slack_mrkdwn",
    "slack_mrkdwn_to_markdown",
    "chat_messages_to_markdown",
    "markdown_to_chat_messages",
    "markdown_to_org",
    "org_to_markdown",
    "markdown_to_mediawiki",
    "mediawiki_to_markdown",
    "markdown_to_jira",
    "jira_to_markdown",
    "table",
    "aligned_table",
    "markdown_table_to_rows",
    "markdown_table_to_records",
    "csv_to_markdown_table",
    "markdown_table_to_csv",
    "markdown_table_statistics",
    "json_to_markdown",
    "markdown_to_json",
    "structured_to_markdown",
    "markdown_to_structured",
    "ini_to_markdown",
    "markdown_to_ini",
    "toml_to_markdown",
    "toml_reader",
    "markdown_to_toml",
    "dotenv_to_markdown",
    "pixiv_to_markdown",
    "markdown_to_dotenv",
    "markdown_to_directory_tree",
    "directory_tree_to_markdown",
    "directory_to_markdown",
    "tree_text_to_markdown",
    "markdown_to_tree_text",
    "scaffold_from_markdown",
    "redis_snapshot_to_markdown",
    "sql_ddl_to_markdown",
    "markdown_to_sql_ddl",
    "markdown_to_ipynb",
    "ipynb_to_markdown",
    "markdown_to_py_percent",
    "py_percent_to_markdown",
    "key_value_table",
    "section",
    "inline_code",
    "json_block",
    "status_line",
    "wrap_section",
    "md_table",
    "md_kv",
    "ALERT_FLAVORS",
    "GITHUB_ALERT_KINDS",
    "QIITA_NOTE_KINDS",
    "ZENN_MESSAGE_KINDS",
    "CHAT_ROLES",
    "split_reasoning",
    "compact_llm_output",
    "llm_output_digest",
    "extract_identifiers",
    "SUPPORTED",
    "UNSUPPORTED",
]

import argparse, ast, calendar as calendar_module, configparser, csv, doctest, email as email_module, email.message as email_message, email.policy as email_policy, email.utils as email_utils, graphlib, html as html_module, importlib.metadata as importlib_metadata, inspect, io, json, math, platform, re, statistics, sys, tokenize, unicodedata
from dataclasses import dataclass; from html.parser import HTMLParser; from pathlib import Path; from typing import Any, Literal; from urllib.parse import urljoin, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

# ---------------------------------------------------------------------------
# Capability notes (kept in-module so a vendored single file stays honest)
# ---------------------------------------------------------------------------

SUPPORTED = {
    "io": ["save_markdown", "read_markdown"],
    "structure": [
        "ATX headings (# ## ...)",
        "fenced code blocks",
        "inline links [text](url)",
        "reference-style link definitions [id]: url",
        "images ![alt](url)",
        "raw HTML tags (best-effort extraction)",
        "horizontal rules (--- *** ___)",
        "context helpers: slugify / table_of_contents / extract_section / strip_prose_keep_structure / minify_markdown / safe_truncate",
        "llm io: split_reasoning / compact_llm_output / llm_output_digest / "
        "extract_identifiers (answer vs. <think>/<thinking>/<reasoning>/Open WebUI "
        "<details type=reasoning> blocks, log-safe compaction, a JSON-able digest, "
        "and UUID/URL/GitHub-ref/hex-SHA identifiers worth referencing instead of "
        "embedding whole content)",
    ],
    "conversion": [
        "link/image builders",
        "HTML <a>/<img> <-> Markdown link/image",
        "conservative html_to_markdown / markdown_to_html for common tags",
        "lightweight HtmlNode / parse_html_dom / dom_to_html / dom_to_markdown / markdown_to_dom",
        "GitHub / Qiita / Zenn / Obsidian alerts and callouts in markdown_to_html "
        "(shared <aside class=\"markdown-alert\"> HTML; alerts win over ordinary > quotes)",
        "ordinary > blockquotes in markdown_to_html (multi-line, blank > lines; "
        "inner ATX/lists/tables stay paragraph text)",
        "simple GFM pipe tables in markdown_to_html "
        "(header + | --- | separator + rows -> <table>/<thead>/<th>/<tbody>/<td>)",
        "strikethrough in markdown_to_html (~~text~~ -> <del>text</del>)",
        "GFM task lists in markdown_to_html "
        "(- [ ] / - [x] / - [X], also * and +; disabled checkbox <input>; mixed with ordinary <li> in one <ul>)",
        "angle-bracket autolinks in markdown_to_html "
        "(<https://...> / <http://...> -> <a href>; bare URLs stay literal)",
        ":::details summary containers in markdown_to_html "
        "(Zenn-style :::details ... ::: -> <details><summary>; raw HTML <details> stays escaped)",
        "GFM/Pandoc-like footnotes in markdown_to_html "
        "([^id] refs + [^id]: definitions -> <sup><a> + <section class=\"footnotes\">)",
        "simple HTML <table> in html_to_markdown (GFM pipe table; headerless rows use an empty header)",
        "HTML <del> -> ~~text~~ and <li><input type=checkbox> -> - [ ] / - [x] in html_to_markdown",
        "heading/paragraph id+class (+ other simple attrs) in html_to_markdown "
        "as Pandoc-style {#id .class key=\"value\"} so markdown_to_kramdown can attach IAL",
        "structured_to_markdown / markdown_to_structured for JSON-compatible Python data "
        "(typed canonical Markdown table; reusable by INI/TOML adapters)",
        "INI / TOML / dotenv <-> canonical structured Markdown adapters "
        "(semantic round-trip; source comments/spacing/quote style are canonicalized)",
        "directory tree <-> nested Markdown list (read-only directory listing, "
        "tree-command text <-> list, traversal-safe non-overwriting scaffold creation)",
        "markdown_to_kramdown: Pandoc/PHP-Extra {#id .class key=value} on headings/paragraphs "
        "-> Kramdown block IAL ({: #id .class key=\"value\"}); ordinary Markdown left alone",
        "kramdown_to_markdown: strip known heading/paragraph IAL back to plain Markdown "
        "(attributes dropped; {:toc} / {::extensions} / Liquid / front matter left as-is)",
        "alert_stylesheet / default_stylesheet (compact CSS strings for markdown_to_html output)",
        "markdown_to_web_ui_v1 (web-ui HTML contract v1 document wrapper; no CSS/runtime dependency)",
    ],
    "generation": [
        "heading / bold / italic / strikethrough / blockquote / horizontal_rule",
        "alert (GitHub > [!NOTE], Qiita :::note, Zenn :::message, Obsidian callouts; "
        "GitLab > [!note] generation)",
        "bullet_list / numbered_list / task_item / task_list",
        "details (Zenn :::details summary / body; markdown_to_html -> <details><summary>)",
        "footnote_ref / footnote ([^id] inline ref and [^id]: definition)",
        "inline_code / code_block / json_block",
        "mermaid_block / extract_mermaid_blocks (opaque Mermaid source only; no parsing/rendering)",
        "structural diagrams: headings -> Mermaid mindmap, task deps -> Mermaid flowchart, "
        "Python classes -> Mermaid classDiagram, Markdown links -> Graphviz DOT",
        "reference generators: inspect object -> API reference, argparse parser -> CLI reference, "
        "installed distribution metadata -> package reference",
        "system formats: calendar month -> table, platform/interpreter facts -> table "
        "(no host/user identity), RFC 5322 email <-> Markdown (multipart/alternative; "
        "never sent, attachments listed not decoded), Markdown -> man(7) troff (request-injection safe)",
        "dialects: Markdown <-> Slack mrkdwn / Org-mode / MediaWiki / Jira wiki on the markdown lite "
        "subset (headings, lines, fences, nested lists, quotes, rules, bold/italic/strike/code/links); "
        "LLM chat messages <-> ## Role sections (string content, known roles, ambiguity rejected)",
        "table / key_value_table",
        "md_table / md_kv (*args-friendly wrappers, no list/dict pre-building needed)",
        "status_line",
        "section (heading + blocks) / wrap_section (tool-detectable markers)",
        "ial / with_attributes (Kramdown {: #id .class key=\"value\"} lines)",
    ],
}

UNSUPPORTED = {
    "parser": [
        "full CommonMark / GFM compliance",
        "backslash escaping (\\* stays literal, does not suppress emphasis)",
        "lazy blockquote continuation (a quoted paragraph must keep > on every line)",
        "nested blockquotes / inner block constructs inside > quotes "
        "(headings, lists, and tables inside a quote stay paragraph text; "
        "GitHub/Obsidian [!type] openers still render as <aside> and win over quotes)",
        "GitLab >>> multiline alert blockquotes (the > [!note] form is generated; "
        "title-less lowercase five-kind markers parse as Obsidian, not a separate GitLab flavor)",
        "nested alert/callout containers (Obsidian > > [!type], Zenn ::::details, Qiita nested :::)",
        "nested emphasis edge cases (asymmetric delimiter runs, whitespace-adjacent "
        "delimiters -- see fixtures/benchmark/commonmark_examples.yaml)",
        "GFM tables without a delimiter row of 3+ hyphens (header-only pipe lines "
        "stay paragraphs; alignment colons are accepted but not emitted as HTML attributes)",
        "html_to_markdown nested tables / colspan / rowspan / <caption> "
        "(nested tables flatten to cell text; extra spans are ignored; caption text is dropped)",
        "numbered-list task items (1. [ ] stays ordinary <li> text; only -/*/ + markers are tasks)",
        "raw HTML <details> in Markdown (escaped, not passed through; use details() / :::details)",
        "nested :::details / ::::details containers (the first ::: closes the block)",
        "footnote lazy continuation (unindented wrapping stays a new paragraph; "
        "only 2+ space / tab indented continuation is kept). Unused definitions are "
        "dropped. Undefined [^id] stays literal. Duplicate ids: first definition wins",
        "bare URL autolinks (https://example.com stays literal; only <https://...> / "
        "<http://...> and [text](url) become <a>)",
        "angle autolinks with schemes other than http/https (mailto:, ftp:, uppercase HTTP://)",
        "unmatched strikethrough (a lone ~~ stays literal; ~~a~~b~~ takes the first pair)",
        "Math / Mermaid rendering (Mermaid helpers only fence/extract opaque source)",
        "full Kramdown (extensions {::comment}/{::options}/{::nomarkdown}, math, "
        "TOC macros {:toc}, span IAL, IAL on lists/quotes/tables, attribute references)",
        "Liquid {% %} / {{ }}, YAML front matter, Jekyll tags / includes / baseurl",
        "llm io helpers are a narrow heuristic, not a provider-format parser: only "
        "<think>/<thinking>/<reasoning> and Open WebUI's <details type=\"reasoning\"> "
        "are recognized as reasoning (other providers' own reasoning formats stay "
        "literal answer text); extract_identifiers pattern-matches UUIDs/URLs/GitHub "
        "refs/hex runs and does not validate them against a real repository, commit, "
        "or hash algorithm",
    ],
    "conversion": [
        "dialect conversions escape nothing inside plain text: literal markup characters "
        "(e.g. a bare * in Jira/Slack text) may be read as markup; tables, raw HTML, "
        "footnotes, Slack headings/ordered-list semantics/fence languages, internal "
        "[[wiki]] links, templates, and Org drawers/keywords are not converted",
        "lossy round-trips for complex nested HTML",
        "browser DOM / HTML5 tree-construction fidelity (HtmlNode is a small normalized tree)",
        "JavaScript / SVG behavior preservation",
        "CSS class and style fidelity (HTML style= is dropped; IAL class names are kept)",
        "raw inline HTML tags are escaped, not passed through, by markdown_to_html",
        "kramdown_to_markdown drops IAL attributes (lossy); markdown_to_html does not "
        "apply IAL as HTML id/class",
    ],
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(?:\s*[-*_]){3,}\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_INLINE_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_INLINE_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_LINKED_IMAGE_RE = re.compile(
    r"\[(?:!\[[^\]]*\]\([^)]+\))\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)"
)
_REF_DEF_RE = re.compile(
    r"^\s*\[([^\]]+)\]:\s*(\S+)(?:\s+\"([^\"]*)\")?\s*$"
)
_REF_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
_REF_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\[([^\]]+)\]")
_ANGLE_URL_RE = re.compile(r"<(https?://[^>\s]+)>")
_BARE_URL_RE = re.compile(r"(?<![\"'(\\[])(https?://[^\s)<>\"]+)")
_DATA_URI_RE = re.compile(r"(?<![A-Za-z0-9_-])data:([^,\s]*),([^\s]*)", re.IGNORECASE)
_DATA_URI_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*/[A-Za-z0-9._+-]+\Z")
_HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>", re.DOTALL)
_HTML_IMG_RE = re.compile(
    r"<img\b([^>]*)/?>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_A_RE = re.compile(
    r"<a\b([^>]*)>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"""([^\s=]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))""",
    re.DOTALL,
)

# GitHub docs: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#alerts
# Qiita cheat sheet (Note): https://qiita.com/Qiita/items/c686397e4a0f4f11683d
# Zenn Markdown guide (メッセージ): https://zenn.dev/zenn/articles/markdown-guide
# Obsidian Help (Callouts): https://help.obsidian.md/callouts
# GitLab GLFM alerts: https://docs.gitlab.com/ee/user/markdown/#alerts
ALERT_FLAVORS = ("github", "qiita", "zenn", "obsidian", "gitlab")
GITHUB_ALERT_KINDS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")
QIITA_NOTE_KINDS = ("info", "warn", "alert")
ZENN_MESSAGE_KINDS = ("message", "alert")
_GITHUB_ALERT_KIND_SET = frozenset(GITHUB_ALERT_KINDS)
_QIITA_NOTE_KIND_SET = frozenset(QIITA_NOTE_KINDS)
_OBSIDIAN_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_BLOCKQUOTE_ALERT_RE = re.compile(
    r"^\s{0,3}>\s*\[!([A-Za-z][A-Za-z0-9_-]*)\]([+-]?)(?:[ \t]+(\S.*?))?[ \t]*$"
)
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^\s{0,3}>( ?)?(.*)$")
_QIITA_NOTE_OPEN_RE = re.compile(
    r"^\s{0,3}:::note(?:\s+(info|warn|alert))?\s*$",
    re.IGNORECASE,
)
_ZENN_MESSAGE_OPEN_RE = re.compile(
    r"^\s{0,3}:::message(?:\s+(alert))?\s*$",
    re.IGNORECASE,
)
_DETAILS_OPEN_RE = re.compile(
    r"^\s{0,3}:::details(?:[ \t]+(.*))?\s*$",
    re.IGNORECASE,
)
_COLON_CONTAINER_CLOSE_RE = re.compile(r"^\s{0,3}:::\s*$")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")
_FOOTNOTE_CONT_RE = re.compile(r"^(?: {2,}|\t)(.*)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")
_LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+")
_TASK_ITEM_RE = re.compile(r"^\[([ xX])\](?:[ \t]+(.*))?$")
_STRIKETHROUGH_RE = re.compile(r"~~((?:(?!~~)[^\n])+?)~~")
_IAL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_IAL_NAME_RE = re.compile(r"^[^\s.#{}]+$")
_ATTR_LIST_TOKEN_RE = re.compile(
    r"#(?P<id>[^\s.#{}]+)"
    r"|\.(?P<cls>[^\s.#{}]+)"
    r"|(?P<key>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:=(?:\"(?P<dval>[^\"]*)\"|'(?P<sval>[^']*)'|(?P<uval>[^\s{}]+)))?"
)
_STANDALONE_IAL_RE = re.compile(r"^\{:(?!:)[ \t]*(?P<inner>[^{}]*)\}\s*$")
_TRAILING_KRAMDOWN_IAL_RE = re.compile(
    r"^(?P<body>.*?)(?P<list>\{:(?!:)[ \t]*(?P<inner>[^{}]*)\})\s*$"
)
_TRAILING_PANDOC_ATTR_RE = re.compile(
    r"^(?P<body>.*?)(?P<list>\{(?![%{:])(?P<inner>[^{}]*)\})\s*$"
)
_HTML_ATTR_SKIP = frozenset({"id", "class", "style"})
_HOST_PORT_REFERENCE_RE = re.compile(
    r"^(?P<host>(?:localhost|(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9A-Fa-f:.]+\]))"
    r":(?P<port>\d{1,5})(?P<rest>(?:[/?#].*)?)$"
)


# === SECTION: scanner ===
# Shared lexical helpers. New format converters should use these helpers
# instead of re-implementing fence state or treating inline code as prose.

@dataclass(frozen=True, slots=True)
class _ScannedLine:
    """One physical Markdown line with its code-context classification."""

    text: str
    in_fenced_code: bool
    is_fence_open: bool = False
    is_fence_close: bool = False


def _advance_fence(active_fence: str | None, line: str) -> tuple[str | None, bool, bool]:
    """Return next fence state, plus whether ``line`` opens or closes it."""
    match = _FENCE_RE.match(line)
    if active_fence is not None:
        if match and line.startswith(active_fence):
            return None, False, True
        return active_fence, False, False
    if match:
        return match.group(1), True, False
    return None, False, False


def _scan_lines(lines: list[str]) -> list[_ScannedLine]:
    """Classify lines as ordinary text or fenced-code context.

    This is deliberately lexical and dependency-free. It preserves the
    repository's existing ``_FENCE_RE`` contract: while a fence is open, a
    line beginning with its marker closes it even if text follows. For
    example, a line with the active triple-backtick marker followed by
    ``inner`` closes it. Callers that require strict Markdown validation
    should reject such malformed input separately.
    """
    active_fence: str | None = None
    scanned: list[_ScannedLine] = []
    for line in lines:
        next_fence, opened, closed = _advance_fence(active_fence, line)
        scanned.append(
            _ScannedLine(
                text=line,
                in_fenced_code=active_fence is not None or opened,
                is_fence_open=opened,
                is_fence_close=closed,
            )
        )
        active_fence = next_fence
    return scanned


def _mask_inline_code(text: str) -> str:
    """Replace balanced backtick code spans with spaces, preserving indexes.

    This conservative helper is for scanners/extractors, not an inline
    renderer. Unbalanced runs stay untouched; fenced code is handled by
    ``_scan_lines`` first.
    """
    chars = list(text)
    index = 0
    while index < len(text):
        if text[index] != "`":
            index += 1
            continue
        end = index
        while end < len(text) and text[end] == "`":
            end += 1
        marker = text[index:end]
        close = text.find(marker, end)
        if close < 0:
            index = end
            continue
        for masked_index in range(index, close + len(marker)):
            chars[masked_index] = " "
        index = close + len(marker)
    return "".join(chars)


def _mask_html_comments(text: str) -> str:
    """Blank HTML comments, preserving newlines so later spans stay aligned.

    A comment runs from ``<!--`` through ``-->``. With no closing ``-->`` it
    runs through EOF, the same way an unterminated HTML comment block does.
    Code protects comment openers; inside a comment, code markers are literal
    and cannot hide its closing delimiter or open a fence in following text.
    """
    out = []
    active_fence = None
    in_comment = False
    for line in text.splitlines(keepends=True):
        if not in_comment:
            next_fence, opened, _closed = _advance_fence(active_fence, line.rstrip("\r\n"))
            in_code = active_fence is not None or opened
            active_fence = next_fence
            if in_code:
                out.append(line)
                continue
        chars = list(line)
        index = 0
        while index < len(line):
            if in_comment or line.startswith("<!--", index):
                end = line.find("-->", index if in_comment else index + 4)
                stop = len(line) if end < 0 else end + 3
                for masked_index in range(index, stop):
                    if chars[masked_index] not in "\r\n":
                        chars[masked_index] = " "
                in_comment = end < 0
                index = stop
            elif line[index] == "`":
                end = index + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                marker = line[index:end]
                close = line.find(marker, end)
                index = end if close < 0 else close + len(marker)
            else:
                index += 1
        out.append("".join(chars))
    return "".join(out)


def _live_text(content: str) -> str:
    """``content`` with fenced code and inline code spans blanked, same length.

    Extractors search this view so that Markdown shown inside code (a ``#``
    comment, a ``[text](url)`` sample) is not reported as a heading, link or
    image, then read the matched text back from ``content`` at the same span.
    HTML comments are blanked too, including one that never closes.
    """
    # Resolve comment/code precedence before blanking code, so a fence or
    # backtick inside a comment cannot erase the comment's closing delimiter.
    raw = _mask_html_comments(content).splitlines(keepends=True)
    bodies = [line.rstrip("\r\n") for line in raw]
    out = []
    for line, body, item in zip(raw, bodies, _scan_lines(bodies)):
        ending = line[len(body):]
        if item.in_fenced_code:
            out.append(" " * len(body) + ending)
        else:
            out.append(_mask_inline_code(body) + ending)
    return "".join(out)


def _live_matches(pattern: re.Pattern[str], live: str, content: str):
    """Matches found in the live view, re-read from ``content`` when possible."""
    for match in pattern.finditer(live):
        original = pattern.match(content, match.start())
        yield original if original is not None and original.end() == match.end() else match


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def save_markdown(content: str, filepath: str) -> str:
    """Save Markdown content to a file."""
    try:
        path = Path(filepath).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error saving file: {e}"
    return f"Saved successfully: {path}"


def read_markdown(filepath: str, count_hashtags: bool = False) -> dict:
    """Read a Markdown file and optionally count heading ``#`` markers.

    Returns:
        dict with keys ``content`` (str), ``hashtag_count`` (int | None),
        ``success`` (bool).
    """
    result: dict[str, Any] = {
        "content": "",
        "hashtag_count": None,
        "success": False,
    }
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        result["content"] = content
        result["success"] = True
        if count_hashtags:
            total = 0
            for line in content.splitlines():
                match = re.match(r"^(#+)", line)
                if match:
                    total += len(match.group(1))
            result["hashtag_count"] = total
    except OSError as e:
        result["content"] = f"Error reading file: {e}"
    return result


def run_markdown_doctest(content: str, name: str = "<markdown>", globs: Any = None) -> doctest.TestResults:
    """Run Python doctest prompts found in fenced Markdown code blocks.

    Only Python-family fences participate. The examples are executed, so this
    helper is for trusted project documentation and test suites only.
    """
    source = _markdown_doctest_source(content)
    test = doctest.DocTestParser().get_doctest(
        source, dict(globs or {}), name, name, 0,
    )
    runner = doctest.DocTestRunner()
    runner.run(test, out=lambda _message: None)
    return doctest.TestResults(runner.failures, runner.tries)


def _markdown_doctest_source(content: str) -> str:
    """Keep Python fence contents while preserving Markdown line numbers.

    Fence state is supplied by the shared P0 scanner rather than a parallel
    fence parser. Fence info strings use their first token as the language.
    """
    lines = content.splitlines(keepends=True)
    scanned = _scan_lines([line.rstrip("\r\n") for line in lines])
    output: list[str] = []
    in_python = False
    for line, item in zip(lines, scanned):
        match = _FENCE_RE.match(item.text)
        if item.is_fence_open:
            info = match.group(2).strip() if match else ""
            language = info.split()[0].lower() if info else ""
            in_python = language in {"python", "python3", "py", "pycon"}
            output.append("\n" if line.endswith("\n") else "")
        elif item.is_fence_close:
            in_python = False
            output.append("\n" if line.endswith("\n") else "")
        elif in_python:
            output.append(item.text + ("\n" if line.endswith("\n") else ""))
        else:
            output.append("\n" if line.endswith("\n") else "")
    return "".join(output)

# ---------------------------------------------------------------------------
# Structure extraction
# ---------------------------------------------------------------------------

def extract_sections(content: str) -> list[dict[str, Any]]:
    """List ATX headings (``level`` + ``title``), without their body text.

    For heading-delimited sections that keep each section's body, use
    ``split_sections()`` instead.
    """
    sections: list[dict[str, Any]] = []
    for line, live in zip(content.splitlines(), _live_text(content).splitlines()):
        # Headings inside fenced code are code, not structure.
        if not live.strip():
            continue
        match = _HEADING_RE.match(live)
        if match:
            sections.append(
                {
                    "level": len(match.group(1)),
                    "title": line[match.start(2):match.end(2)].strip(),
                }
            )
    return sections


def split_sections(content: str) -> list[dict[str, Any]]:
    """Split content into heading-delimited sections, each with its body text.

    Each item has ``level``, ``title``, and ``content`` (the heading line
    plus everything up to the next heading of any level). The prelude
    before the first heading is returned with ``level`` 0 and an empty
    ``title`` when it is non-empty. For a heading list without body text,
    use ``extract_sections()`` instead.
    """
    lines = content.splitlines(keepends=True)
    parts: list[dict[str, Any]] = []
    current = {"level": 0, "title": "", "content": ""}
    body: list[str] = []

    def flush() -> None:
        text = "".join(body)
        if current["level"] == 0 and current["title"] == "" and text.strip() == "":
            return
        item = dict(current)
        item["content"] = text
        parts.append(item)

    for line in lines:
        match = _HEADING_RE.match(line.rstrip("\n"))
        if match:
            flush()
            current = {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "content": "",
            }
            body = [line]
        else:
            body.append(line)
    flush()
    return parts


def split_slides(content: str) -> list[dict[str, Any]]:
    """Split content into pages at thematic breaks (``---``, ``***``, ``___``).

    This is how slide Markdown (Marp, reveal.js, Deckset) separates slides.
    Each item has ``content`` (the page without the break lines, outer blank
    lines trimmed), ``title`` and ``level`` (the heading the page starts
    with; ``""`` and 0 without one) and ``body`` (the page after that
    heading), so a page maps onto a title box and a body box.

    A break inside fenced code is text. A ``---`` line directly under a line
    of text is that line's setext underline, not a break: leave a blank line
    above a break. YAML front matter at the very top (``---`` to ``---`` or
    ``...``, with at least one ``key:`` line) holds the deck's settings and
    is skipped. A leading or trailing break adds no page; two breaks in a
    row make an empty page.
    """
    lines = content.splitlines()
    start = 0
    if lines and lines[0].rstrip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].rstrip() in ("---", "...")), None)
        if closing is not None and any(re.match(r"[A-Za-z_][\w -]*:(\s|$)", line) for line in lines[1:closing]):
            start = closing + 1
    lines = lines[start:]
    pages: list[list[str]] = [[]]
    for index, item in enumerate(_scan_lines(lines)):
        line = lines[index]
        above = lines[index - 1] if index else ""
        underline = line.strip().startswith("-") and above.strip() != "" and not _HR_RE.match(above) and not _FENCE_RE.match(above)
        if _HR_RE.match(line) and not item.in_fenced_code and not item.is_fence_open and not item.is_fence_close and not underline:
            pages.append([])
        else:
            pages[-1].append(line)
    texts = ["\n".join(page).strip("\n") for page in pages]
    while texts and not texts[0].strip():
        texts.pop(0)
    while texts and not texts[-1].strip():
        texts.pop()
    slides = []
    for text in texts:
        first, _, rest = text.partition("\n")
        match = _HEADING_RE.match(first)
        slides.append({
            "level": len(match.group(1)) if match else 0,
            "title": match.group(2).strip() if match else "",
            "body": rest.strip("\n") if match else text,
            "content": text,
        })
    return slides


def _parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(attr_text):
        key = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        attrs[key] = html_module.unescape(value)
    return attrs


def _reference_map(content: str) -> dict[str, dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for line in _live_text(content).splitlines():
        match = _REF_DEF_RE.match(line)
        if match:
            key = match.group(1).strip().lower()
            refs[key] = {
                "url": match.group(2).strip("<>"),
                "title": match.group(3) or "",
            }
    return refs


def extract_links(content: str) -> list[dict[str, Any]]:
    """Extract inline, reference-style, angle-autolink and HTML links (not bare images).

    Links shown inside fenced code or inline code spans are not reported.
    """
    refs = _reference_map(content)
    live = _live_text(content)
    links: list[dict[str, Any]] = []

    # Linked images: [![alt](img)](href) — record the outer link.
    for match in _live_matches(_LINKED_IMAGE_RE, live, content):
        links.append(
            {
                "text": "",
                "url": match.group(1),
                "title": match.group(2) or "",
                "style": "linked-image",
            }
        )

    # Mask constructs that would confuse the inline-link regex.
    masked = _LINKED_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), live)
    masked = _INLINE_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), masked)
    masked = _REF_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), masked)

    for match in _live_matches(_INLINE_LINK_RE, masked, content):
        text = match.group(1)
        links.append(
            {
                "text": text,
                "url": match.group(2),
                "title": match.group(3) or "",
                "style": "inline",
            }
        )

    for match in _live_matches(_REF_LINK_RE, masked, content):
        text = match.group(1)
        key = (match.group(2) or text).strip().lower()
        ref = refs.get(key, {})
        links.append(
            {
                "text": text,
                "url": ref.get("url", ""),
                "title": ref.get("title", ""),
                "style": "reference",
                "ref": key,
            }
        )

    for match in _ANGLE_URL_RE.finditer(masked):
        links.append({"text": match.group(1), "url": match.group(1), "title": "", "style": "autolink"})

    for match in _live_matches(_HTML_A_RE, live, content):
        attrs = _parse_attrs(match.group(1))
        href = attrs.get("href", "")
        if href:
            links.append(
                {
                    "text": re.sub(r"<[^>]+>", "", match.group(2)).strip(),
                    "url": href,
                    "title": attrs.get("title", ""),
                    "style": "html",
                }
            )
    return links


def extract_images(content: str) -> list[dict[str, Any]]:
    """Extract Markdown and HTML images (not those shown inside code)."""
    refs = _reference_map(content)
    live = _live_text(content)
    images: list[dict[str, Any]] = []

    for match in _live_matches(_INLINE_IMAGE_RE, live, content):
        images.append(
            {
                "alt": match.group(1),
                "url": match.group(2),
                "title": match.group(3) or "",
                "style": "inline",
            }
        )

    for match in _live_matches(_REF_IMAGE_RE, live, content):
        alt = match.group(1)
        key = match.group(2).strip().lower()
        ref = refs.get(key, {})
        images.append(
            {
                "alt": alt,
                "url": ref.get("url", ""),
                "title": ref.get("title", ""),
                "style": "reference",
                "ref": key,
            }
        )

    for match in _live_matches(_HTML_IMG_RE, live, content):
        attrs = _parse_attrs(match.group(1))
        src = attrs.get("src", "")
        if src:
            images.append(
                {
                    "alt": attrs.get("alt", ""),
                    "url": src,
                    "title": attrs.get("title", ""),
                    "style": "html",
                    "width": attrs.get("width"),
                    "height": attrs.get("height"),
                }
            )
    return images


def extract_code_blocks(content: str) -> list[dict[str, Any]]:
    """Extract fenced code blocks (``` or ~~~)."""
    blocks: list[dict[str, Any]] = []
    lines = content.splitlines()
    scanned = _scan_lines(lines)
    i = 0
    while i < len(lines):
        if not scanned[i].is_fence_open:
            i += 1
            continue
        match = _FENCE_RE.match(lines[i])
        assert match is not None  # guaranteed by _scan_lines
        fence = match.group(1)
        info = match.group(2).strip()
        lang = info.split()[0] if info else ""
        body: list[str] = []
        i += 1
        while i < len(lines) and not scanned[i].is_fence_close:
            body.append(lines[i])
            i += 1
        blocks.append(
            {
                "language": lang,
                "info": info,
                "code": "\n".join(body),
            }
        )
        if i < len(lines):
            i += 1
    return blocks


def extract_raw_html(content: str) -> list[dict[str, Any]]:
    """Best-effort list of raw HTML tags found in the document."""
    found: list[dict[str, Any]] = []
    for match in _HTML_TAG_RE.finditer(content):
        tag = match.group(1).lower()
        snippet = match.group(0)
        # Skip tags that appear inside fenced code by a cheap heuristic:
        # handled via inventory after code stripping when needed.
        found.append({"tag": tag, "snippet": snippet})
    return found


def extract_urls(content: str, *, base_url: str | None = None) -> list[str]:
    """Collect unique URLs from links, images, autolinks, and bare URLs."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        url = url.strip()
        if not url:
            return
        if base_url:
            url = urljoin(base_url, url)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for item in extract_links(content):
        add(item.get("url", ""))
    for item in extract_images(content):
        add(item.get("url", ""))
    for match in _ANGLE_URL_RE.finditer(content):
        add(match.group(1))
    for match in _BARE_URL_RE.finditer(content):
        add(match.group(1).rstrip(".,;:)"))
    return urls


def extract_data_uris(content: str) -> list[dict[str, str]]:
    """List well-formed literal data URIs without decoding their payloads.

    The helper is observation-only: it does not fetch, decode, or execute
    a URI. A missing media type uses the RFC default ``text/plain``.
    Payload punctuation is preserved; only explicit surrounding delimiters
    and an unmatched Markdown closing parenthesis are removed.
    """
    found: list[dict[str, str]] = []
    for match in _DATA_URI_RE.finditer(content):
        metadata = match.group(1)
        parts = metadata.split(";") if metadata else []
        declared_type = parts[0].lower() if parts and parts[0] else ""
        if declared_type and not _DATA_URI_MEDIA_TYPE_RE.match(declared_type):
            continue
        if any(part and "=" not in part and part.lower() != "base64" for part in parts[1:]):
            continue
        uri = match.group(0).rstrip("\"'>")
        # Drop unmatched Markdown ")" chars and any prose punctuation
        # trailing *after* them (e.g. "...hello)." from "(data:...,hello).",
        # or "...hello))" from a prose paren wrapping a "(...)" link/image),
        # but keep payload punctuation that sits *before* them (e.g. the "!"
        # in "(data:...,Hello!)" is data, not a delimiter). Multiple stacked
        # wrappers can leave more than one unmatched ")" at the end, so cut
        # at the excess-th ")" from the right in the trailing run, not
        # always just the last one.
        trim = len(uri)
        while trim > 0 and uri[trim - 1] in ").,;:!?":
            trim -= 1
        tail = uri[trim:]
        paren_positions = [i for i, char in enumerate(tail) if char == ")"]
        excess = uri.count(")") - uri.count("(")
        if paren_positions and excess > 0:
            cut_at = paren_positions[-min(excess, len(paren_positions))]
            uri = uri[: trim + cut_at]
            # Removing a wrapping ")" can expose a quote/angle delimiter
            # that was only hidden behind it, e.g. "(<data:...,x>)".
            uri = uri.rstrip("\"'>")
        found.append({
            "uri": uri,
            "media_type": declared_type or "text/plain",
            "metadata": metadata,
        })
    return found


def slugify(text: str) -> str:
    """Convert supported inline heading text to a GitHub-style anchor slug.

    Lowercase Unicode letters, retain numbers/combining marks, hyphens and
    underscores, remove other punctuation/symbols, and replace each ASCII space
    with a hyphen. Other whitespace is removed. The lightweight inline parser
    handles emphasis, inline code, images and inline links; raw HTML, reference
    links and platform-specific extensions are not resolved by this helper.

    >>> slugify("Hello World!")
    'hello-world'
    >>> slugify("User & Permissions (v2.0)")
    'user--permissions-v20'
    """
    cleaned = _lite_plain(_lite_inline(str(text))).strip().lower()
    chars = []
    for char in cleaned:
        category = unicodedata.category(char)
        if category[0] in {"L", "N", "M"} or char in "-_":
            chars.append(char)
        elif char == " ":
            chars.append("-")
    return "".join(chars)


def table_of_contents(
    content: str,
    *,
    min_level: int = 1,
    max_level: int = 6,
) -> str:
    """Generate a Markdown bullet list from live top-level ATX headings.

    Fenced code and HTML comments are ignored. Up to three leading spaces and
    optional closing hashes are accepted. Duplicate slugs are allocated across
    all ATX headings before level filtering, including collisions with numeric
    suffixes. Indentation is relative to the included heading hierarchy.

    Labels are escaped plain text, so heading links cannot create nested links.
    Inline text follows ``slugify``'s lightweight subset. Setext headings,
    nested headings, and raw HTML block semantics are outside the supported
    input subset; this is not a complete GitHub Markdown renderer.

    :param content: Markdown source string.
    :param min_level: Minimum heading level (1..6) to include.
    :param max_level: Maximum heading level (1..6) to include.
    """
    if not isinstance(min_level, int) or isinstance(min_level, bool) or not 1 <= min_level <= 6:
        raise ValueError("min_level must be an integer between 1 and 6")
    if not isinstance(max_level, int) or isinstance(max_level, bool) or not 1 <= max_level <= 6:
        raise ValueError("max_level must be an integer between 1 and 6")
    if min_level > max_level:
        raise ValueError("min_level cannot be greater than max_level")

    lines = content.splitlines()
    live_lines = _mask_html_comments(content).splitlines()
    scanned = _scan_lines(live_lines)
    heading_re = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
    slug_counts: dict[str, int] = {}
    items: list[str] = []
    level_stack: list[int] = []

    for line, live, scan in zip(lines, live_lines, scanned):
        if scan.in_fenced_code:
            continue
        # Match original inline code, not whitespace masking: greedy whitespace
        # after the ATX marker otherwise swallows a code-only heading's title.
        match = heading_re.match(live)
        if not match:
            continue
        lvl = len(match.group(1))
        title = line[match.start(2):match.end(2)].strip() if match.group(2) is not None else ""
        # Remove live inline comments without interpreting literal code samples.
        comment_spans = list(re.finditer(r"<!--.*?(?:-->|$)", _mask_inline_code(title)))
        for comment in reversed(comment_spans):
            title = title[:comment.start()] + title[comment.end():]
        title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", title).strip()
        base_slug = slugify(title)
        slug = base_slug
        while slug in slug_counts:
            slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
            slug = f"{base_slug}-{slug_counts[base_slug]}"
        slug_counts[slug] = 0
        if not min_level <= lvl <= max_level:
            continue

        while level_stack and level_stack[-1] > lvl:
            level_stack.pop()
        if not level_stack or level_stack[-1] < lvl:
            level_stack.append(lvl)

        indent = "  " * (len(level_stack) - 1)
        link_label = html_module.escape(_lite_plain(_lite_inline(title)), quote=False)
        link_label = re.sub(r"([\\`*_\[\]])", r"\\\1", link_label)
        items.append(f"{indent}- [{link_label}](#{slug})")

    return "\n".join(items) + ("\n" if items else "")


def inventory(content: str) -> dict[str, Any]:
    """Summarize common Markdown constructs present in ``content``."""
    sections = extract_sections(content)
    links = extract_links(content)
    images = extract_images(content)
    codes = extract_code_blocks(content)
    # Avoid counting HTML that only appears inside fenced code.
    scrubbed = content
    for block in codes:
        scrubbed = scrubbed.replace(block["code"], "")
    html_tags = extract_raw_html(scrubbed)
    hr_count = sum(1 for line in content.splitlines() if _HR_RE.match(line))
    return {
        "headings": sections,
        "heading_count": len(sections),
        "links": links,
        "link_count": len(links),
        "images": images,
        "image_count": len(images),
        "code_blocks": codes,
        "code_block_count": len(codes),
        "raw_html": html_tags,
        "raw_html_count": len(html_tags),
        "horizontal_rule_count": hr_count,
        "urls": extract_urls(content),
        "supported": SUPPORTED,
        "unsupported": UNSUPPORTED,
    }

def extract_section(
    content: str,
    heading_name: str,
    *,
    level: int | None = None,
    partial: bool = False,
) -> str:
    """Return one ATX heading section, including nested child headings.

    Matching is case-insensitive and exact by default. Set partial=True for a
    case-insensitive substring match, and level to restrict heading depth. The
    section ends at the next heading with a level less than or equal to the
    selected heading. A missing or empty heading name returns an empty string.
    """
    wanted = str(heading_name).strip()
    if not wanted:
        return ""

    selected_start: int | None = None
    selected_level: int | None = None
    lines = content.splitlines(keepends=True)

    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        current_level = len(match.group(1))
        title = match.group(2).strip()
        if level is not None and current_level != level:
            continue
        normalized_title = title.casefold()
        normalized_wanted = wanted.casefold()
        matched = (
            normalized_wanted in normalized_title
            if partial
            else normalized_title == normalized_wanted
        )
        if matched:
            selected_start = index
            selected_level = current_level
            break

    if selected_start is None or selected_level is None:
        return ""

    selected_end = len(lines)
    for index in range(selected_start + 1, len(lines)):
        match = _HEADING_RE.match(lines[index].rstrip("\r\n"))
        if match and len(match.group(1)) <= selected_level:
            selected_end = index
            break

    result = "".join(lines[selected_start:selected_end])
    if result and not result.endswith(("\n", "\r")):
        result += "\n"
    return result


def strip_prose_keep_structure(content: str) -> str:
    """Keep Markdown structure while dropping ordinary prose lines.

    Headings, lists, blockquotes, thematic breaks, and complete fenced code
    blocks are preserved verbatim. Lines inside a fenced block are always
    retained, including prose-looking lines. This is a deterministic
    extraction heuristic, not a summarizer.
    """
    kept: list[str] = []
    active_fence: str | None = None

    for line in content.splitlines():
        fence = _FENCE_RE.match(line)
        if active_fence is not None:
            kept.append(line)
            if fence and line.startswith(active_fence):
                active_fence = None
            continue

        if fence:
            kept.append(line)
            active_fence = fence.group(1)
            continue

        if not line.strip():
            if kept and kept[-1] != "":
                kept.append("")
            continue

        if (
            _HEADING_RE.match(line)
            or _HR_RE.match(line)
            or re.match(r"^\s*(?:[-+*]\s+|\d+[.)]\s+|>)", line)
        ):
            kept.append(line)

    while kept and kept[-1] == "":
        kept.pop()

    if not kept:
        return ""
    result = "\n".join(kept)
    return result + ("\n" if content.endswith(("\n", "\r")) else "")


def minify_markdown(content: str, strip_html: bool = True) -> str:
    """Apply conservative physical Markdown minification.

    When strip_html is true, HTML comments are removed. Three or more
    consecutive newlines are reduced to two, and surrounding whitespace is
    stripped. No Markdown parsing or semantic conversion is performed.
    """
    if strip_html:
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def safe_truncate(content: str, max_chars: int) -> str:
    """Truncate to max_chars while closing an open fenced code block.

    The length limit is hard: when there is enough room, a matching closing
    fence is appended within the limit. If the limit is too small to retain
    both the prefix and a closing fence, the raw prefix is returned.
    ValueError is raised for a negative limit.
    """
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(content) <= max_chars:
        return content

    prefix = content[:max_chars]
    active_fence: str | None = None
    for line in prefix.splitlines():
        match = _FENCE_RE.match(line)
        if not match:
            continue
        marker = match.group(1)
        if active_fence is None:
            active_fence = marker
        elif line.startswith(active_fence):
            active_fence = None

    if active_fence is None:
        return prefix

    suffix = ("" if prefix.endswith(("\n", "\r")) else "\n") + active_fence
    if len(suffix) > max_chars:
        return prefix
    return prefix[: max_chars - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Builders & focused HTML <-> Markdown helpers
# ---------------------------------------------------------------------------

def make_link(text: str, url: str, title: str | None = None) -> str:
    """Build a Markdown inline link."""
    if title:
        return f'[{text}]({url} "{title}")'
    return f"[{text}]({url})"


def make_image(alt: str, url: str, title: str | None = None) -> str:
    """Build a Markdown image."""
    if title:
        return f'![{alt}]({url} "{title}")'
    return f"![{alt}]({url})"


def html_image_to_markdown(html: str) -> str:
    """Convert an ``<img>`` tag (or HTML snippet containing one) to Markdown."""
    match = _HTML_IMG_RE.search(html)
    if not match:
        return ""
    attrs = _parse_attrs(match.group(1))
    return make_image(attrs.get("alt", ""), attrs.get("src", ""), attrs.get("title") or None)


def markdown_image_to_html(
    markdown: str,
    *,
    width: str | None = None,
    height: str | None = None,
) -> str:
    """Convert a Markdown image (or image URL string) to an ``<img>`` tag."""
    match = _INLINE_IMAGE_RE.search(markdown.strip())
    if match:
        alt, url, title = match.group(1), match.group(2), match.group(3) or ""
    else:
        # Treat bare path/URL as image source.
        alt, url, title = "", markdown.strip(), ""
    safe_url = _sanitize_url_scheme(url)
    if not safe_url:
        return html_module.escape(alt)
    attrs = [
        f'src="{html_module.escape(safe_url, quote=True)}"',
        f'alt="{html_module.escape(alt, quote=True)}"',
    ]
    if title:
        attrs.append(f'title="{html_module.escape(title, quote=True)}"')
    if width:
        attrs.append(f'width="{html_module.escape(width, quote=True)}"')
    if height:
        attrs.append(f'height="{html_module.escape(height, quote=True)}"')
    return "<img " + " ".join(attrs) + " />"


def html_link_to_markdown(html: str) -> str:
    """Convert an ``<a>...</a>`` tag to a Markdown link."""
    match = _HTML_A_RE.search(html)
    if not match:
        return ""
    attrs = _parse_attrs(match.group(1))
    text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
    return make_link(text, attrs.get("href", ""), attrs.get("title") or None)


def markdown_link_to_html(markdown: str) -> str:
    """Convert a Markdown inline link to an ``<a>`` tag."""
    match = _INLINE_LINK_RE.search(markdown.strip())
    if not match:
        return ""
    text, url, title = match.group(1), match.group(2), match.group(3) or ""
    safe_url = _sanitize_url_scheme(url)
    if not safe_url:
        return html_module.escape(text)
    attrs = [f'href="{html_module.escape(safe_url, quote=True)}"']
    if title:
        attrs.append(f'title="{html_module.escape(title, quote=True)}"')
    return f"<a {' '.join(attrs)}>{html_module.escape(text)}</a>"


# ---------------------------------------------------------------------------
# Markdown generation building blocks
# ---------------------------------------------------------------------------

def heading(text: str, level: int = 1) -> str:
    """Build an ATX heading (``level`` is clamped to 1-6).

    >>> heading("Title")
    '# Title\\n'
    >>> heading("Sub", level=2)
    '## Sub\\n'
    """
    level = max(1, min(level, 6))
    return "#" * level + " " + str(text) + "\n"


def bold(text: str) -> str:
    """Wrap ``text`` in ``**bold**`` markers."""
    return f"**{text}**"


def italic(text: str) -> str:
    """Wrap ``text`` in ``*italic*`` markers."""
    return f"*{text}*"


def strikethrough(text: str) -> str:
    """Wrap ``text`` in ``~~strikethrough~~`` markers.

    ``markdown_to_html()`` turns this into ``<del>``. Unmatched ``~~``
    stays literal; a run like ``~~a~~b~~`` takes the first pair.
    """
    return f"~~{text}~~"


def blockquote(text: str) -> str:
    """Prefix every line of ``text`` with ``> `` to form a blockquote.

    ``markdown_to_html()`` turns this into ``<blockquote>``. Blank lines
    become blank ``>`` lines and split paragraphs. A ``> [!TYPE]`` opener
    is an alert, not a quote.
    """
    lines = str(text).splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines) + "\n"


def _normalize_alert_flavor(flavor: str) -> str:
    name = str(flavor).strip().lower()
    if name not in ALERT_FLAVORS:
        raise ValueError(
            f"unknown alert flavor {flavor!r}; expected one of {ALERT_FLAVORS}"
        )
    return name


def _colon_container(open_line: str, text: str) -> str:
    body = str(text)
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{open_line}\n{body}:::\n"


def _blockquote_alert_markdown(marker: str, text: str) -> str:
    body = str(text)
    if not body:
        return marker + "\n"
    return marker + "\n" + blockquote(body)


def alert(
    kind: str,
    text: str = "",
    *,
    flavor: str = "github",
    title: str | None = None,
    fold: str | None = None,
) -> str:
    """Build a site-flavored Markdown alert / callout / note block.

    ``flavor`` selects the syntax (default GitHub). ``kind`` is
    case-insensitive for the fixed vocabularies; unknown kinds raise
    ``ValueError`` except Obsidian, which allows custom type identifiers
    (Obsidian itself falls unknown types back to ``note`` visually).

    GitHub (docs.github.com, Alerts)::

        > [!NOTE]
        > Useful information that users should know

    Kinds: ``NOTE``, ``TIP``, ``IMPORTANT``, ``WARNING``, ``CAUTION``.
    Emitted uppercase. Same-line titles and fold markers are not part of
    GitHub's syntax and raise ``ValueError``.

    Qiita (qiita.com/Qiita/items/c686397e4a0f4f11683d, Note)::

        :::note info
        インフォメーション
        :::

    Kinds: ``info`` (also ``note``), ``warn``, ``alert``. ``info`` is
    optional in Qiita's parser; this helper emits ``:::note info`` for
    the info kind so the type is explicit.

    Zenn (zenn.dev/zenn/articles/markdown-guide, メッセージ)::

        :::message
        メッセージをここに
        :::

        :::message alert
        警告メッセージをここに
        :::

    Kinds: ``message`` (also ``info`` / empty) or ``alert``.

    Obsidian (help.obsidian.md/callouts)::

        > [!note]
        > body

        > [!warning] Custom title
        > body

    Kind identifiers are emitted lowercase. Optional ``title`` is written
    on the marker line. Optional ``fold`` is ``+`` (expanded) or ``-``
    (collapsed), immediately after ``[!type]``. Custom types matching
    ``[A-Za-z][A-Za-z0-9_-]*`` are allowed.

    GitLab (docs.gitlab.com, GLFM Alerts) is generation-only: the same
    five kinds as GitHub, emitted lowercase, with optional ``title``.
    GitLab's ``>>>`` multiline blockquote alerts are not generated.
    Parsed title-less lowercase five-kind markers are classified as
    Obsidian (see ``markdown_to_html``); uppercase title-less five-kind
    markers are classified as GitHub.

    Multi-line ``text``: GitHub/Obsidian/GitLab prefix each line with
    ``> `` (blank lines become ``>``); Qiita/Zenn keep the body verbatim
    between the opening and closing ``:::``.
    """
    flavor_name = _normalize_alert_flavor(flavor)
    if fold is not None and fold not in {"+", "-"}:
        raise ValueError("fold must be '+' or '-' when set")
    if title is not None:
        title = str(title)
    kind_raw = str(kind).strip()

    if flavor_name == "github":
        if title is not None:
            raise ValueError("GitHub alerts do not support a same-line title")
        if fold is not None:
            raise ValueError("GitHub alerts do not support Obsidian fold markers")
        canonical = kind_raw.upper()
        if canonical not in _GITHUB_ALERT_KIND_SET:
            raise ValueError(
                f"unknown GitHub alert kind {kind!r}; "
                f"expected one of {GITHUB_ALERT_KINDS}"
            )
        return _blockquote_alert_markdown(f"> [!{canonical}]", text)

    if flavor_name == "gitlab":
        if fold is not None:
            raise ValueError("GitLab alerts do not support Obsidian fold markers")
        canonical = kind_raw.upper()
        if canonical not in _GITHUB_ALERT_KIND_SET:
            raise ValueError(
                f"unknown GitLab alert kind {kind!r}; "
                f"expected one of {GITHUB_ALERT_KINDS}"
            )
        marker = f"> [!{canonical.lower()}]"
        if title:
            marker += f" {title}"
        return _blockquote_alert_markdown(marker, text)

    if flavor_name == "qiita":
        if title is not None or fold is not None:
            raise ValueError("Qiita notes do not support title= or fold=")
        qiita_kind = kind_raw.lower()
        if qiita_kind in {"", "note"}:
            qiita_kind = "info"
        if qiita_kind not in _QIITA_NOTE_KIND_SET:
            raise ValueError(
                f"unknown Qiita note kind {kind!r}; "
                f"expected one of {QIITA_NOTE_KINDS}"
            )
        return _colon_container(f":::note {qiita_kind}", text)

    if flavor_name == "zenn":
        if title is not None or fold is not None:
            raise ValueError("Zenn messages do not support title= or fold=")
        zenn_kind = kind_raw.lower()
        if zenn_kind in {"", "message", "info"}:
            return _colon_container(":::message", text)
        if zenn_kind == "alert":
            return _colon_container(":::message alert", text)
        raise ValueError(
            f"unknown Zenn message kind {kind!r}; "
            f"expected one of {ZENN_MESSAGE_KINDS}"
        )

    # obsidian
    if not _OBSIDIAN_KIND_RE.fullmatch(kind_raw):
        raise ValueError(
            f"invalid Obsidian callout type {kind!r}; "
            "expected a letter followed by letters, digits, '_' or '-'"
        )
    fold_mark = fold or ""
    marker = f"> [!{kind_raw.lower()}]{fold_mark}"
    if title:
        marker += f" {title}"
    return _blockquote_alert_markdown(marker, text)


def horizontal_rule() -> str:
    """Return a thematic break (``---``)."""
    return "---\n"


def bullet_list(items: Any) -> str:
    """Build an unordered (``-``) list from an iterable of strings."""
    lines = [f"- {item}" for item in items]
    return "\n".join(lines) + ("\n" if lines else "")


def numbered_list(items: Any) -> str:
    """Build an ordered (``1.``) list from an iterable of strings."""
    lines = [f"{i}. {item}" for i, item in enumerate(items, start=1)]
    return "\n".join(lines) + ("\n" if lines else "")


def task_item(text: str = "", *, checked: bool = False) -> str:
    """Build one GFM task-list line (``- [ ] text`` / ``- [x] text``).

    ``markdown_to_html()`` turns this into a ``<ul>`` item with a
    disabled checkbox (``checked`` when ``checked`` is true). The box
    is not interactive.
    """
    mark = "x" if checked else " "
    body = str(text)
    if body:
        return f"- [{mark}] {body}\n"
    return f"- [{mark}]\n"


def task_list(items: Any) -> str:
    """Build a GFM task list from strings or ``(text, checked)`` pairs.

    A bare string is an unchecked item. Empty ``items`` yields ``""``.
    Mixed task and ordinary bullets in one source list stay one ``<ul>``
    with mixed ``<li>`` shapes (see ``markdown_to_html``).
    """
    lines: list[str] = []
    for item in items:
        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                text, checked = item[0], bool(item[1])
            elif len(item) == 1:
                text, checked = item[0], False
            else:
                text, checked = "", False
            lines.append(task_item(text, checked=checked).rstrip("\n"))
        else:
            lines.append(task_item(item, checked=False).rstrip("\n"))
    return "\n".join(lines) + ("\n" if lines else "")


def details(summary: str, body: str = "") -> str:
    """Build a collapsible section using Zenn's ``:::details`` form.

    ``markdown_to_html()`` turns this into ``<details><summary>…</summary>…``.
    Summary and body inlines (bold / italic / code / links) are parsed; nested
    block constructs inside the body stay paragraph text (small contract).
    Raw HTML ``<details>`` in Markdown input stays escaped — this helper is
    the supported way to emit the element.

    Nested ``:::details`` / ``::::details`` is unsupported: the first ``:::``
    closer ends the block.
    """
    summary_line = str(summary).replace("\n", " ").strip()
    opener = f":::details {summary_line}" if summary_line else ":::details"
    body_text = str(body)
    if not body_text.strip():
        return f"{opener}\n:::\n"
    if not body_text.endswith("\n"):
        body_text += "\n"
    return f"{opener}\n{body_text}:::\n"


def footnote_ref(ident: str) -> str:
    """Build an inline footnote reference (``[^id]``).

    ``markdown_to_html()`` turns a defined ref into a superscript link.
    Undefined ids stay literal (documented degrade).
    """
    return f"[^{ident}]"


def footnote(ident: str, text: str = "") -> str:
    """Build a footnote definition line (``[^id]: text``).

    Pair with :func:`footnote_ref` (or a hand-written ``[^id]``) in the
    body. Duplicate ids: first definition wins. Unused definitions are
    dropped from HTML. Continuation lines may be indented with 2+ spaces
    or a tab; unindented wrapping is a new paragraph, not a continuation.
    """
    return f"[^{ident}]: {text}\n"


def inline_code(text: str) -> str:
    """Wrap ``text`` in single backticks for inline code."""
    return f"`{text}`"


_FENCE_CHARS = ("`", "~")


def _adaptive_fence(code: str, fence_char: str = "`") -> str:
    """A fence of ``fence_char`` one longer than the longest run already in ``code``.

    ``_FENCE_RE`` (and this module's own fence-closing scan) accept any
    length-3-or-more fence, but only close on a line starting with a run of
    the same fence character at least as long as the opening fence -- so a
    fixed triple-backtick fence is ambiguous the moment ``code`` itself
    contains a triple-backtick run (e.g. Markdown source shown as an example
    inside a code block). Picking a fence longer than anything already in
    ``code`` keeps that scan unambiguous with no changes to the reading side.

    ``fence_char`` must be a single backtick or tilde -- the only two
    characters Markdown recognizes as a fence at all (``_FENCE_RE``). Any
    other value raises ``ValueError`` rather than silently building a fence
    nothing would ever close (a multi-character ``fence_char`` isn't a valid
    fence line, and an empty one breaks the run-length regex outright).
    """
    if fence_char not in _FENCE_CHARS:
        raise ValueError(f"fence_char must be one of {_FENCE_CHARS!r}, got {fence_char!r}")
    longest = 0
    for run in re.findall(re.escape(fence_char) + r"+", code):
        longest = max(longest, len(run))
    return fence_char * max(3, longest + 1)


def code_block(code: str, lang: str = "", *, fence_char: str = "`") -> str:
    """Wrap ``code`` in a fenced code block, optionally tagged with ``lang``.

    The fence length adapts to ``code``'s content: if it already contains a
    run of ``fence_char`` as long as (or longer than) a plain triple fence
    -- typically Markdown-inside-Markdown, like a fenced example embedded in
    a doc -- the fence grows just enough to stay unambiguous, the same way
    CommonMark itself requires. Plain content keeps the original triple
    fence, so existing callers see no change. Pass ``fence_char="~"`` for a
    tilde fence (e.g. when ``code`` itself contains backtick runs).

    ``lang`` is a single-line info string and may include metadata or Unicode.
    Backtick fences cannot have backticks in their info string (CommonMark
    section 4.5). Tilde info may contain both markers, but a leading tilde
    needs separating whitespace, e.g. ``lang=" ~tag"``, so it does not extend
    only the opening fence. Existing whitespace is preserved in the output.

    :raises ValueError: if ``fence_char`` isn't ``"`"`` or ``"~"`` -- the
        only two characters Markdown recognizes as a fence -- or ``lang``
        contains a line separator, a backtick for a backtick fence, or an
        unseparated leading tilde for a tilde fence.
    """
    fence = _adaptive_fence(code, fence_char)
    if lang and lang.splitlines() != [lang]:
        raise ValueError("lang must be a single line without line separators")
    if fence_char == "`" and "`" in lang:
        raise ValueError("lang cannot contain backticks with a backtick fence")
    if fence_char == "~" and lang.startswith("~"):
        raise ValueError("lang must separate a leading tilde from the fence with whitespace")
    return f"{fence}{lang}\n{code}\n{fence}\n"


def mermaid_block(source: str, *, fence_char: str = "`") -> str:
    """Wrap opaque Mermaid source in a safe ``mermaid`` fenced block.

    Line endings are normalized to LF. Mermaid syntax is not parsed or
    validated, and no renderer, browser, JavaScript runtime, or I/O is used.
    """
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return code_block(normalized, lang="mermaid", fence_char=fence_char)


def extract_mermaid_blocks(content: str) -> list[str]:
    """Return Mermaid fenced-block sources in document order.

    The info-string language match is case-insensitive and must be exactly
    ``mermaid`` as its first token. Non-Mermaid fences and inline code are
    ignored. Returned line endings follow the module-wide LF convention.
    """
    return [
        block["code"]
        for block in extract_code_blocks(content)
        if block["language"].lower() == "mermaid"
    ]


def _diagram_label(text: Any) -> str:
    """Escape a label for quoted Mermaid/DOT output."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _live_markdown_lines(content: str) -> list[tuple[str, str]]:
    """Return ``(raw, inline-masked)`` lines outside fenced code."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    scanned = _scan_lines(lines)
    result: list[tuple[str, str]] = []
    for line, item in zip(lines, scanned):
        if item.in_fenced_code:
            continue
        result.append((line, _mask_inline_code(line)))
    return result


def markdown_headings_to_mermaid_mindmap(content: str, *, root: str = "Document") -> str:
    """Generate deterministic Mermaid mindmap source from live ATX headings."""
    headings: list[tuple[int, str]] = []
    for raw, masked in _live_markdown_lines(content):
        match = _HEADING_RE.match(masked)
        if match:
            headings.append((len(match.group(1)), raw[len(match.group(1)):].strip()))

    out = ["mindmap", f'  root["{_diagram_label(root)}"]']
    stack: list[tuple[int, str]] = []
    for index, (level, title) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        depth = len(stack) + 2
        node_id = f"n{index}"
        out.append("  " * depth + f'{node_id}["{_diagram_label(title)}"]')
        stack.append((level, node_id))
    return "\n".join(out) + "\n"


_TASK_DEP_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(.+?)(?:\s+<-\s+(.+))?\s*$")


def markdown_tasks_to_mermaid_flowchart(content: str) -> str:
    """Generate Mermaid flowchart source from narrow GFM task dependencies.

    Syntax: ``- [ ] Deploy <- Build, Test`` means Deploy depends on Build and
    Test. Every referenced dependency must also appear as a task item.
    """
    tasks: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for raw, masked in _live_markdown_lines(content):
        match = _TASK_DEP_RE.match(masked)
        if not match:
            continue
        name = raw[match.start(1):match.end(1)].strip()
        deps_text = match.group(2)
        if " <- " in name:
            name = name.split(" <- ", 1)[0].rstrip()
        if not name or name in seen:
            raise ValueError(f"Task names must be unique and non-empty: {name!r}")
        seen.add(name)
        deps = [part.strip() for part in deps_text.split(",")] if deps_text else []
        if any(not dep for dep in deps):
            raise ValueError(f"Empty dependency for task {name!r}")
        tasks.append((name, deps))

    names = {name for name, _ in tasks}
    missing = sorted({dep for _, deps in tasks for dep in deps if dep not in names})
    if missing:
        raise ValueError("Unknown task dependencies: " + ", ".join(missing))

    graph = {name: set(deps) for name, deps in tasks}
    try:
        tuple(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        raise ValueError("Task dependency cycle detected") from exc

    ids = {name: f"n{index}" for index, (name, _) in enumerate(tasks)}
    out = ["flowchart TD"]
    for name, _ in tasks:
        out.append(f'  {ids[name]}["{_diagram_label(name)}"]')
    for name, deps in tasks:
        for dep in deps:
            out.append(f"  {ids[dep]} --> {ids[name]}")
    return "\n".join(out) + "\n"


def _ast_base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _ast_base_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def python_to_mermaid_class_diagram(source: str) -> str:
    """Generate Mermaid ``classDiagram`` source from Python class structure."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python source: {exc}") from exc

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    known_names = [node.name for node in classes]
    external_bases: list[str] = []
    for node in classes:
        for base in node.bases:
            name = _ast_base_name(base)
            if name and name not in known_names and name not in external_bases:
                external_bases.append(name)

    ids: dict[str, str] = {}
    for index, name in enumerate(known_names + external_bases):
        ids[name] = f"c{index}"

    out = ["classDiagram"]
    for node in classes:
        class_id = ids[node.name]
        out.append(f'  class {class_id}["{_diagram_label(node.name)}"] {{')
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [arg.arg for arg in item.args.args]
                if args and args[0] in {"self", "cls"}:
                    args = args[1:]
                out.append(f"    {item.name}({', '.join(args)})")
        out.append("  }")
    for name in external_bases:
        out.append(f'  class {ids[name]}["{_diagram_label(name)}"]')
    for node in classes:
        for base in node.bases:
            name = _ast_base_name(base)
            if name:
                out.append(f"  {ids[name]} <|-- {ids[node.name]}")
    return "\n".join(out) + "\n"


def markdown_links_to_dot(content: str) -> str:
    """Generate a deterministic DOT link graph grouped by current heading.

    Links before the first heading belong to ``Document``. If a syntactically
    matched ATX heading becomes empty after stripping, that section also falls
    back to ``Document`` instead of creating an empty node label.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    scanned = _scan_lines(lines)
    current = "Document"
    edges: list[tuple[str, str, str]] = []
    refs = _reference_map(normalized)
    for line, item in zip(lines, scanned):
        if item.in_fenced_code:
            continue
        masked = _mask_inline_code(line)
        heading_match = _HEADING_RE.match(masked)
        if heading_match:
            current = line[len(heading_match.group(1)):].strip() or "Document"
            continue
        safe = _INLINE_IMAGE_RE.sub(lambda m: " " * len(m.group(0)), masked)
        for match in _INLINE_LINK_RE.finditer(safe):
            edges.append((current, match.group(2), match.group(1)))
        for match in _REF_LINK_RE.finditer(safe):
            text_value = match.group(1)
            key = (match.group(2) or text_value).strip().lower()
            target = refs.get(key, {}).get("url", "")
            if target:
                edges.append((current, target, text_value))

    sources: list[str] = []
    targets: list[str] = []
    for source, target, _ in edges:
        if source not in sources:
            sources.append(source)
        if target not in targets:
            targets.append(target)
    source_ids = {name: f"s{i}" for i, name in enumerate(sources)}
    target_ids = {url: f"u{i}" for i, url in enumerate(targets)}

    out = ["digraph markdown_links {"]
    for name in sources:
        out.append(f'  {source_ids[name]} [label="{_diagram_label(name)}"];')
    for url in targets:
        out.append(f'  {target_ids[url]} [label="{_diagram_label(url)}"];')
    for source, target, label in edges:
        out.append(
            f'  {source_ids[source]} -> {target_ids[target]} [label="{_diagram_label(label)}"];'
        )
    out.append("}")
    return "\n".join(out) + "\n"


def _reference_summary(value: Any) -> str:
    """Return the first non-empty documentation line for a known object."""
    try:
        doc = inspect.getdoc(value) or ""
    except (AttributeError, TypeError):
        return ""
    return next((line.strip() for line in doc.splitlines() if line.strip()), "")


def _reference_signature(value: Any) -> str:
    """Return a stable signature when ``inspect.signature`` supports value."""
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return ""


def _reference_member(value: Any) -> Any:
    """Unwrap class/static methods without invoking descriptors."""
    if isinstance(value, (classmethod, staticmethod)):
        return value.__func__
    return value


def inspect_to_markdown(obj: Any, *, title: str | None = None) -> str:
    """Generate a small API reference for an already-provided Python object.

    Module/class members are read from ``vars()`` so properties and other
    descriptors are not invoked. No module discovery or dynamic import occurs.
    """
    if inspect.ismodule(obj):
        kind = "module"
    elif inspect.isclass(obj):
        kind = "class"
    elif inspect.isfunction(obj):
        kind = "function"
    elif inspect.ismethod(obj):
        kind = "method"
    elif inspect.isbuiltin(obj):
        kind = "builtin"
    else:
        kind = type(obj).__name__

    name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or type(obj).__name__
    module_name = getattr(obj, "__module__", "") or ""
    doc = inspect.getdoc(obj) or ""
    output = [heading(title or f"API: {name}")]
    metadata_rows = [["Name", name], ["Kind", kind]]
    if module_name:
        metadata_rows.append(["Module", module_name])
    signature = _reference_signature(obj)
    if signature:
        metadata_rows.append(["Signature", signature])
    output.append(table(["Field", "Value"], metadata_rows))
    if doc:
        output.append(heading("Description", 2))
        output.append(doc.rstrip() + "\n")

    members: list[list[str]] = []
    if inspect.ismodule(obj) or inspect.isclass(obj):
        owner_module = getattr(obj, "__name__", "") if inspect.ismodule(obj) else ""
        for member_name, raw_value in sorted(vars(obj).items()):
            if member_name.startswith("_"):
                continue
            value = _reference_member(raw_value)
            if not (inspect.isfunction(value) or inspect.isclass(value) or inspect.isbuiltin(value)):
                continue
            if owner_module and getattr(value, "__module__", owner_module) != owner_module:
                continue
            member_kind = "class" if inspect.isclass(value) else "function"
            members.append([
                member_name,
                member_kind,
                _reference_signature(value),
                _reference_summary(value),
            ])
    if members:
        output.append(heading("Public API", 2))
        output.append(table(["Name", "Kind", "Signature", "Summary"], members))
    return "\n".join(part.rstrip("\n") for part in output if part) + "\n"


def _argparse_value(value: Any) -> str:
    """Render argparse metadata without unstable object reprs."""
    if value == argparse.SUPPRESS:
        return "SUPPRESS"
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_argparse_value(item) for item in value)
    return type(value).__name__


def _argparse_type_name(value: Any) -> str:
    """Return a compact, stable display name for an argparse ``type`` value."""
    if value is None:
        return ""
    name = getattr(value, "__name__", None)
    if name:
        return str(name)
    return type(value).__name__


def _argparse_action_name(action: argparse.Action) -> str:
    """Normalize common argparse action classes to user-facing action names."""
    mapping = {
        "_StoreAction": "store",
        "_StoreConstAction": "store_const",
        "_StoreTrueAction": "store_true",
        "_StoreFalseAction": "store_false",
        "_AppendAction": "append",
        "_AppendConstAction": "append_const",
        "_CountAction": "count",
        "_HelpAction": "help",
        "_VersionAction": "version",
        "BooleanOptionalAction": "boolean_optional",
    }
    return mapping.get(action.__class__.__name__, action.__class__.__name__.lstrip("_"))


def argparse_to_markdown(parser: argparse.ArgumentParser, *, title: str | None = None) -> str:
    """Generate deterministic CLI reference Markdown from an ArgumentParser.

    The parser is never executed. Reading ``_actions`` is the only intentional
    narrow dependency on argparse private state; public parser metadata and
    ``format_usage()`` are used otherwise. Type, metavar, and common action
    kinds are rendered as stable descriptive names rather than object reprs.
    """
    if not isinstance(parser, argparse.ArgumentParser):
        raise TypeError("parser must be an argparse.ArgumentParser")

    output = [heading(title or f"CLI: {parser.prog}")]
    if parser.description:
        output.append(str(parser.description).rstrip() + "\n")
    usage = parser.format_usage().strip()
    if usage:
        output.append(heading("Usage", 2))
        output.append(code_block(usage, lang="text"))

    argument_rows: list[list[str]] = []
    subcommands: list[list[str]] = []
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            for command, child in sorted(action.choices.items()):
                subcommands.append([command, child.description or ""])
            continue
        if action.dest == argparse.SUPPRESS:
            continue
        label = ", ".join(action.option_strings) if action.option_strings else action.dest
        choices = ""
        if action.choices is not None:
            try:
                choices = ", ".join(str(choice) for choice in action.choices)
            except TypeError:
                choices = _argparse_value(action.choices)
        argument_rows.append([
            label,
            "yes" if action.required else "no",
            _argparse_value(action.nargs),
            choices,
            _argparse_value(action.default),
            _argparse_type_name(action.type),
            _argparse_value(action.metavar),
            _argparse_action_name(action),
            "" if action.help == argparse.SUPPRESS else str(action.help or ""),
        ])

    if argument_rows:
        output.append(heading("Arguments", 2))
        output.append(table(
            ["Argument", "Required", "Nargs", "Choices", "Default", "Type", "Metavar", "Action", "Help"],
            argument_rows,
        ))
    if subcommands:
        output.append(heading("Subcommands", 2))
        output.append(table(["Command", "Description"], subcommands))
    if parser.epilog:
        output.append(heading("Epilog", 2))
        output.append(str(parser.epilog).rstrip() + "\n")
    return "\n".join(part.rstrip("\n") for part in output if part) + "\n"


def distribution_to_markdown(name: str, *, title: str | None = None) -> str:
    """Generate package metadata Markdown for an installed distribution."""
    if not str(name).strip():
        raise ValueError("distribution name must be non-empty")
    try:
        dist = importlib_metadata.distribution(str(name))
    except importlib_metadata.PackageNotFoundError as exc:
        raise ValueError(f"Distribution not found: {name}") from exc

    metadata = dist.metadata
    package_name = metadata.get("Name") or str(name)
    output = [heading(title or f"Package: {package_name}")]
    fields = [
        ("Name", package_name),
        ("Version", dist.version),
        ("Summary", metadata.get("Summary", "")),
        ("Requires-Python", metadata.get("Requires-Python", "")),
        ("License", metadata.get("License", "")),
        ("Author", metadata.get("Author", "")),
        ("Author-email", metadata.get("Author-email", "")),
        ("Home-page", metadata.get("Home-page", "")),
    ]
    output.append(table(["Field", "Value"], [[key, value] for key, value in fields if value]))

    project_urls = sorted(metadata.get_all("Project-URL") or [])
    if project_urls:
        output.append(heading("Project URLs", 2))
        rows = []
        for value in project_urls:
            label, separator, url = value.partition(",")
            rows.append([label.strip() if separator else "", url.strip() if separator else value.strip()])
        output.append(table(["Label", "URL"], rows))

    requirements = sorted(dist.requires or [])
    if requirements:
        output.append(heading("Requires", 2))
        output.append(bullet_list(requirements))

    entry_points = sorted(dist.entry_points, key=lambda item: (item.group, item.name, item.value))
    if entry_points:
        output.append(heading("Entry Points", 2))
        output.append(table(
            ["Group", "Name", "Value"],
            [[item.group, item.name, item.value] for item in entry_points],
        ))
    return "\n".join(part.rstrip("\n") for part in output if part) + "\n"


def json_block(obj: Any, indent: int = 2) -> str:
    """Serialize ``obj`` as JSON and wrap it in a ```json fenced code block."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    return code_block(text, lang="json")


def table(headers: Any, rows: Any) -> str:
    """Build a Markdown (GFM-style) table.

    ``headers`` is a sequence of column names; ``rows`` is a sequence of
    sequences of cell values (converted with ``str``). Short rows are padded
    with empty cells; extra cells beyond ``len(headers)`` are dropped.

    ``markdown_to_html()`` parses this pipe-table form into
    ``<table>/<thead>/<th>/<tbody>/<td>``.

    >>> table(["a", "b"], [[1, 2], [3, 4]])
    '| a | b |\\n| --- | --- |\\n| 1 | 2 |\\n| 3 | 4 |\\n'
    """
    headers = [_escape_table_cell(h) for h in headers]
    if not headers:
        return ""
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = [_escape_table_cell(v) for v in row][: len(headers)]
        cells += [""] * (len(headers) - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


# === SECTION: table ===

def _display_width(value: Any) -> int:
    """Return a terminal-style display width for a Markdown table cell.

    Combining marks and format characters (zero-width space, ZWJ, variation
    selectors) take no column; full-width and wide East Asian characters take
    two. Ambiguous-width characters deliberately stay one column so the result
    is stable across common Western and Japanese terminals. Emoji sequences
    count once: a skin-tone modifier or a ZWJ-joined part adds nothing, and
    VS16 (emoji presentation) widens a narrow base to two. Cross-checked
    against the npm ``string-width`` package in ``tests/test_html.py``.
    """
    width = 0
    previous = 0
    pictographic = False
    joined = False
    for char in str(value):
        if char == "\u200d":
            # ZWJ glues emoji into one glyph; between letters it is just invisible.
            joined = pictographic
            continue
        if char == "\ufe0f":
            if previous == 1:
                width += 1
                previous = 2
            continue
        if (unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me", "Cf"}
                or (previous and 0x1F3FB <= ord(char) <= 0x1F3FF)):
            continue
        if joined:
            joined = False
            continue
        previous = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        pictographic = ord(char) >= 0x1F000 or 0x2600 <= ord(char) <= 0x27BF
        width += previous
    return width


def _escape_table_cell(value: Any) -> str:
    """Escape a cell for the small GFM pipe-table subset used by this module."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def aligned_table(headers: Any, rows: Any) -> str:
    """Build a pipe table aligned by display width, including CJK cells.

    This is presentation-oriented: parsing it with :func:`markdown_table_to_rows`
    recovers the escaped cell text, but physical padding is not preserved.
    """
    header_cells = [_escape_table_cell(header) for header in headers]
    if not header_cells:
        return ""
    body = []
    for row in rows:
        cells = [_escape_table_cell(value) for value in row][: len(header_cells)]
        body.append(cells + [""] * (len(header_cells) - len(cells)))
    widths = [
        max(3, _display_width(header_cells[index]), *(_display_width(row[index]) for row in body))
        for index in range(len(header_cells))
    ]

    def render(cells: list[str]) -> str:
        return "| " + " | ".join(
            cell + " " * (widths[index] - _display_width(cell))
            for index, cell in enumerate(cells)
        ) + " |"

    out = [render(header_cells), "| " + " | ".join("-" * width for width in widths) + " |"]
    out.extend(render(row) for row in body)
    return "\n".join(out) + "\n"


def markdown_table_to_rows(content: str) -> tuple[list[str], list[list[str]]]:
    """Read the first live GFM pipe table as ``(headers, rows)``.

    A table must have a header and a ``| --- |`` delimiter row. Fenced
    examples, malformed tables, and all text after the first table are
    ignored. Escaped pipes become literal pipes.
    """
    lines = content.splitlines()
    scanned = _scan_lines(lines)
    for index, line in enumerate(lines):
        if scanned[index].in_fenced_code or not _is_table_start(lines, index):
            continue
        headers = _split_table_row(line)
        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip() or not _looks_like_table_row(candidate):
                break
            if scanned[cursor].in_fenced_code or _is_table_separator(candidate):
                break
            cells = _split_table_row(candidate)[: len(headers)]
            rows.append(cells + [""] * (len(headers) - len(cells)))
            cursor += 1
        return headers, rows
    return [], []


def markdown_table_to_records(content: str) -> list[dict[str, str]]:
    """Read the first GFM pipe table as dictionaries keyed by its headers.

    Duplicate headers have no lossless dictionary representation and raise
    ``ValueError`` rather than silently overwriting a column.
    """
    headers, rows = markdown_table_to_rows(content)
    if len(set(headers)) != len(headers):
        raise ValueError("Markdown table headers must be unique for records")
    return [dict(zip(headers, row)) for row in rows]


def csv_to_markdown_table(content: str, *, align: bool = False) -> str:
    """Convert CSV text to a GFM pipe table using the first row as headers.

    CSV quoting is handled by :mod:`csv`; a blank CSV document produces an
    empty result. Set ``align=True`` for East Asian display-width padding.
    """
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return ""
    renderer = aligned_table if align else table
    return renderer(rows[0], rows[1:])


def markdown_table_to_csv(content: str) -> str:
    """Convert the first live GFM pipe table to RFC-style CSV text."""
    headers, rows = markdown_table_to_rows(content)
    if not headers:
        return ""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def markdown_to_ipynb(content: str, *, indent: int | None = 2) -> str:
    """Make a minimal nbformat-4 JSON notebook from Markdown and Python fences.

    Fenced-code state comes from the shared ``_scan_lines`` scanner. The
    converter does not execute cells, create outputs, or infer kernels.
    """
    lines = content.splitlines(keepends=True)
    scanned = _scan_lines([line.rstrip("\r\n") for line in lines])
    cells: list[dict[str, Any]] = []
    prose: list[str] = []
    index = 0
    while index < len(lines):
        item = scanned[index]
        match = _FENCE_RE.match(item.text)
        info = match.group(2).strip() if match else ""
        language = info.split()[0].lower() if info else ""
        if item.is_fence_open and language in {"python", "py", "python3"}:
            if prose:
                cells.append({"cell_type": "markdown", "metadata": {}, "source": prose})
                prose = []
            index += 1
            code: list[str] = []
            while index < len(lines) and not scanned[index].is_fence_close:
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            cells.append({
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": code,
            })
            continue
        prose.append(lines[index])
        index += 1
    if prose:
        cells.append({"cell_type": "markdown", "metadata": {}, "source": prose})
    notebook = {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, ensure_ascii=False, indent=indent) + "\n"


def ipynb_to_markdown(notebook: str | dict[str, Any]) -> str:
    """Render Markdown and code cell sources from a minimal nbformat notebook.

    Outputs and execution counts are deliberately ignored.
    """
    data = json.loads(notebook) if isinstance(notebook, str) else notebook
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        raise ValueError("notebook must be an nbformat object with a cells list")
    parts: list[str] = []
    for cell in data["cells"]:
        if not isinstance(cell, dict):
            continue
        source = cell.get("source", [])
        text = source if isinstance(source, str) else "".join(source)
        if cell.get("cell_type") == "markdown":
            parts.append(text)
        elif cell.get("cell_type") == "code":
            parts.append("```python\n" + text + ("" if text.endswith("\n") or not text else "\n") + "```\n")
    return "".join(parts)


_PY_PERCENT_MARKER_RE = re.compile(r"^#\s*%%(?:\s+\[markdown\])?\s*$")


def _py_percent_marker_rows(content: str) -> dict[int, str]:
    """Return 1-based marker rows mapped to markdown or code.

    tokenize is used only to recognize real Python comment tokens, so text
    such as "# %%" inside a string never becomes a cell boundary.
    """
    rows: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT or token.start[1] != 0:
                continue
            text = token.string.strip()
            if not _PY_PERCENT_MARKER_RE.fullmatch(text):
                continue
            rows[token.start[0]] = "markdown" if "[markdown]" in text else "code"
    except (tokenize.TokenError, IndentationError) as exc:
        raise ValueError(f"Invalid py:percent Python source: {exc}") from exc
    return rows


def markdown_to_py_percent(content: str) -> str:
    """Convert Markdown/Python fences to a narrow Jupytext py:percent form.

    The existing Markdown -> ipynb converter defines the cell split. Markdown
    cells become # %% [markdown] blocks with line comments; Python cells
    become # %% blocks and keep their source text unchanged.
    """
    notebook = json.loads(markdown_to_ipynb(content))
    out: list[str] = []
    for cell in notebook["cells"]:
        source = cell.get("source", [])
        text = source if isinstance(source, str) else "".join(source)
        if cell.get("cell_type") == "markdown":
            out.append("# %% [markdown]\n")
            for line in text.splitlines(keepends=True):
                body = line.rstrip("\r\n")
                ending = "\n" if line.endswith(("\n", "\r")) else ""
                out.append(("# " + body if body else "#") + ending)
            if text and not text.endswith(("\n", "\r")):
                out.append("\n")
        elif cell.get("cell_type") == "code":
            out.append("# %%\n")
            out.append(text)
            if text and not text.endswith(("\n", "\r")):
                out.append("\n")
    return "".join(out)


def py_percent_to_markdown(content: str) -> str:
    """Convert the canonical narrow py:percent subset back to Markdown.

    Marker comments must start in column zero. Markdown-cell payload lines must
    be blank or comment lines. Code-cell source is copied verbatim; it is never
    parsed through AST/unparse, so ordinary Python comments are preserved.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    marker_rows = _py_percent_marker_rows(normalized)
    if not marker_rows:
        raise ValueError("No py:percent cell markers found")

    lines = normalized.splitlines(keepends=True)
    marker_indices = sorted(row - 1 for row in marker_rows)
    parts: list[str] = []
    for offset, marker_index in enumerate(marker_indices):
        kind = marker_rows[marker_index + 1]
        end = marker_indices[offset + 1] if offset + 1 < len(marker_indices) else len(lines)
        body = lines[marker_index + 1:end]
        if kind == "markdown":
            for line_number, line in enumerate(body, marker_index + 2):
                raw = line[:-1] if line.endswith("\n") else line
                if raw == "":
                    parts.append("\n" if line.endswith("\n") else "")
                elif raw == "#":
                    parts.append("\n" if line.endswith("\n") else "")
                elif raw.startswith("# "):
                    parts.append(raw[2:] + ("\n" if line.endswith("\n") else ""))
                else:
                    raise ValueError(
                        f"py:percent markdown cell line {line_number} must be a comment"
                    )
        else:
            code = "".join(body)
            parts.append("~~~python\n")
            parts.append(code)
            if code and not code.endswith("\n"):
                parts.append("\n")
            parts.append("~~~\n")
    return "".join(parts)

# === SECTION: structured snapshots ===

def markdown_table_statistics(content: str) -> dict[str, Any]:
    """Return a small, non-mutating summary of the first Markdown table.

    Numeric aggregates appear only when every non-empty value in a column
    parses as a finite number (``nan``/``inf`` spellings are treated as
    non-numeric text, not data); no schema inference or value conversion
    is applied.

    :raises ValueError: if two columns share a header -- ``numeric_columns``
        is keyed by header text, which cannot represent both losslessly
        (same contract as :func:`markdown_table_to_records`).
    """
    headers, data_rows = markdown_table_to_rows(content)
    if not headers:
        return {"rows": 0, "columns": 0, "headers": [], "numeric_columns": {}}
    if len(set(headers)) != len(headers):
        raise ValueError("Markdown table headers must be unique for statistics")
    numeric_columns: dict[str, dict[str, float | int]] = {}
    for index, header in enumerate(headers):
        values = [row[index] for row in data_rows if index < len(row) and row[index] != ""]
        if not values:
            continue
        try:
            numbers = [float(value) for value in values]
        except ValueError:
            continue
        if not all(math.isfinite(number) for number in numbers):
            continue
        numeric_columns[header] = {
            "count": len(numbers),
            "min": min(numbers),
            "max": max(numbers),
            "mean": statistics.mean(numbers),
            "median": statistics.median(numbers),
        }
    return {
        "rows": len(data_rows),
        "columns": len(headers),
        "headers": headers,
        "numeric_columns": numeric_columns,
    }

_SQL_IDENTIFIER_RE = (
    r'(?:"(?:""|[^"])*"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]|[A-Za-z_][A-Za-z0-9_$]*)'
)
_CREATE_TABLE_NAME_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(" + _SQL_IDENTIFIER_RE + r"(?:\." + _SQL_IDENTIFIER_RE + r")*)",
    re.IGNORECASE,
)
_SQL_TRAILING_NEWLINES_RE = re.compile(
    r"<!-- markdown\.py:sql-trailing-newlines=(\d+) -->"
)


def json_to_markdown(value: Any, title: str = "JSON") -> str:
    """Wrap JSON data as a Markdown document without performing I/O."""
    return heading(title) + json_block(value)


def markdown_to_json(content: str) -> Any:
    """Read the first JSON fence emitted by :func:`json_to_markdown`."""
    for block in extract_code_blocks(content):
        if block["language"].lower() == "json":
            return json.loads(block["code"])
    raise ValueError("No fenced JSON block found")



_STRUCTURED_TABLE_HEADERS = ["id", "parent", "slot", "type", "value"]
_STRUCTURED_MARKER = "<!-- markdown.py:structured-v1 -->\n"
_STRUCTURED_SCALAR_TYPES = {"str", "int", "float", "bool", "null"}


def _structured_scalar_type(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return None


def _structured_json_dumps(value: Any) -> str:
    """Encode one structured scalar/key with stable JSON settings."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _structured_json_loads(text: str, *, what: str) -> Any:
    """Decode one JSON-backed structured field with a focused error."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Structured {what} is not valid JSON") from exc


def _structured_encode_scalar(value: Any) -> tuple[str, str]:
    kind = _structured_scalar_type(value)
    if kind is None:
        raise TypeError(
            "structured data must use JSON-compatible dict/list/scalar values"
        )
    return kind, _structured_json_dumps(value)


def _structured_decode_scalar(kind: str, encoded: str) -> Any:
    if kind not in _STRUCTURED_SCALAR_TYPES:
        raise ValueError(f"Unknown structured node type: {kind!r}")
    value = _structured_json_loads(encoded, what="scalar value")
    actual = _structured_scalar_type(value)
    if actual != kind:
        raise ValueError(
            f"Structured scalar type mismatch: declared {kind}, got {actual}"
        )
    return value


def _structured_validate(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("structured mappings require string keys")
            _structured_validate(child)
        return
    if isinstance(value, list):
        for child in value:
            _structured_validate(child)
        return
    _structured_encode_scalar(value)


def structured_to_markdown(value: Any, title: str = "Structured data") -> str:
    """Render JSON-compatible Python data as a canonical typed Markdown table.

    The representation is intended as a small common layer for format adapters
    such as INI and TOML. Mapping keys must be strings. Supported values are
    dict, list, str, int, finite float, bool and None. Object order and list
    order are preserved.

    This is a semantic round-trip format, not a source-text preservation
    format: whitespace, comments, quoting style, and source-format trivia from
    an upstream format belong to that adapter's declared lossiness contract.
    """
    _structured_validate(value)
    rows: list[list[str]] = []

    def visit(node: Any, parent: str = "", slot: str = "") -> None:
        node_id = str(len(rows))
        if isinstance(node, dict):
            rows.append([node_id, parent, slot, "dict", ""])
            for key, child in node.items():
                visit(child, node_id, _structured_json_dumps(key))
            return
        if isinstance(node, list):
            rows.append([node_id, parent, slot, "list", ""])
            for index, child in enumerate(node):
                visit(child, node_id, str(index))
            return

        kind, encoded = _structured_encode_scalar(node)
        rows.append([node_id, parent, slot, kind, encoded])

    visit(value)
    return heading(title) + _STRUCTURED_MARKER + table(_STRUCTURED_TABLE_HEADERS, rows)


def markdown_to_structured(content: str) -> Any:
    """Restore data emitted by structured_to_markdown.

    A live structured marker must be paired with the immediately following
    canonical pipe table. Node ids and rows use contiguous pre-order traversal;
    that ordering is part of the canonical representation. Malformed node ids,
    parent references, slots, type tags, or scalar JSON values raise ValueError
    instead of being guessed.
    """
    lines = content.splitlines()
    scanned = _scan_lines(lines)
    marker = _STRUCTURED_MARKER.strip()
    marker_index = next(
        (
            index
            for index, line in enumerate(lines)
            if not scanned[index].in_fenced_code and line.strip() == marker
        ),
        None,
    )
    if marker_index is None:
        raise ValueError("No structured-data marker found")

    table_index = marker_index + 1
    while table_index < len(lines) and not lines[table_index].strip():
        table_index += 1
    if table_index >= len(lines) or not _is_table_start(lines, table_index):
        raise ValueError("Structured-data marker is not paired with a table")

    headers, rows = markdown_table_to_rows("\n".join(lines[table_index:]))
    if headers != _STRUCTURED_TABLE_HEADERS:
        raise ValueError("No canonical structured-data table found")
    if not rows:
        raise ValueError("Structured-data table has no root node")

    nodes: list[Any] = []
    for expected_id, row in enumerate(rows):
        raw_id, raw_parent, slot, kind, encoded = row
        try:
            node_id = int(raw_id)
        except ValueError as exc:
            raise ValueError("Structured node id must be an integer") from exc
        if node_id != expected_id:
            raise ValueError("Structured node ids must be contiguous and ordered")

        if kind == "dict":
            if encoded:
                raise ValueError("Structured container rows must have empty values")
            node: Any = {}
        elif kind == "list":
            if encoded:
                raise ValueError("Structured container rows must have empty values")
            node = []
        else:
            node = _structured_decode_scalar(kind, encoded)

        if expected_id == 0:
            if raw_parent or slot:
                raise ValueError("Structured root node cannot have parent or slot")
            nodes.append(node)
            continue

        try:
            parent_id = int(raw_parent)
        except ValueError as exc:
            raise ValueError("Structured parent id must be an integer") from exc
        if parent_id < 0 or parent_id >= len(nodes):
            raise ValueError("Structured parent must reference an earlier node")

        parent = nodes[parent_id]
        if isinstance(parent, dict):
            key = _structured_json_loads(slot, what="mapping slot")
            if not isinstance(key, str):
                raise ValueError("Structured mapping slot must decode to a string")
            if key in parent:
                raise ValueError("Structured mapping contains a duplicate key")
            parent[key] = node
        elif isinstance(parent, list):
            try:
                index = int(slot)
            except ValueError as exc:
                raise ValueError("Structured list slot must be an integer") from exc
            if index != len(parent):
                raise ValueError("Structured list slots must be contiguous and ordered")
            parent.append(node)
        else:
            raise ValueError("Structured scalar nodes cannot have children")

        nodes.append(node)

    return nodes[0]



_INI_DEFAULT_SENTINEL = "\\x00markdown.py:no-default\\x00"
_DOTENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _ini_loads(content: str) -> dict[str, dict[str, str]]:
    """Parse the supported INI subset without interpolation or case folding."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.default_section = _INI_DEFAULT_SENTINEL
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise ValueError(f"Invalid INI: {exc}") from exc
    return {
        section_name: dict(parser.items(section_name, raw=True))
        for section_name in parser.sections()
    }


def _ini_dumps(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("INI root must be a mapping of sections")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.default_section = _INI_DEFAULT_SENTINEL
    for section_name, options in value.items():
        if not isinstance(section_name, str) or "\n" in section_name or "\r" in section_name:
            raise ValueError("INI section names must be single-line strings")
        if section_name == _INI_DEFAULT_SENTINEL:
            raise ValueError("INI section name is reserved")
        if not isinstance(options, dict):
            raise ValueError("INI sections must contain key/value mappings")
        parser.add_section(section_name)
        for key, item in options.items():
            if (
                not isinstance(key, str)
                or not key
                or "\n" in key
                or "\r" in key
                or "=" in key
                or ":" in key
            ):
                raise ValueError("INI keys must be non-empty single-line strings without = or :")
            if not isinstance(item, str):
                raise ValueError("INI values must be strings")
            parser.set(section_name, key, item)
    stream = io.StringIO()
    parser.write(stream, space_around_delimiters=False)
    return stream.getvalue()


def ini_to_markdown(content: str, title: str = "INI") -> str:
    """Convert INI to canonical structured Markdown.

    Section names, key case, and string values round-trip. Comments, delimiter
    choice, and whitespace are intentionally canonicalized. [DEFAULT] is
    treated as an ordinary section so implicit interpolation/inheritance is not
    invented during conversion.
    """
    return structured_to_markdown(_ini_loads(content), title)


def markdown_to_ini(content: str) -> str:
    """Convert canonical structured Markdown to deterministic INI text."""
    return _ini_dumps(markdown_to_structured(content))


def _dotenv_loads(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _DOTENV_KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid dotenv assignment on line {line_number}")
        if key in result:
            raise ValueError(f"Duplicate dotenv key: {key}")

        raw_value = raw_value.strip()
        if raw_value.startswith('"'):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid quoted dotenv value on line {line_number}") from exc
            if not isinstance(value, str):
                raise ValueError(f"Dotenv values must be strings on line {line_number}")
        elif raw_value.startswith("'"):
            if len(raw_value) < 2 or not raw_value.endswith("'"):
                raise ValueError(f"Invalid quoted dotenv value on line {line_number}")
            value = raw_value[1:-1]
        else:
            value = raw_value
        result[key] = value
    return result


def _dotenv_dumps(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("dotenv root must be a mapping")
    lines: list[str] = []
    for key, item in value.items():
        if not isinstance(key, str) or not _DOTENV_KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid dotenv key: {key!r}")
        if not isinstance(item, str):
            raise ValueError("dotenv values must be strings")
        lines.append(f"{key}={_structured_json_dumps(item)}")
    return "".join(line + "\n" for line in lines)


def _pixiv_inline(text: str) -> str:
    def link(match):
        label = match[1].strip()
        target = (match[2] or label).strip().replace('(', '%28').replace(')', '%29').replace(' ', '%20')
        return f'[{label}]({target})'
    def prose(part):
        part = re.sub(r'NEXT▶︎(?=\[\[)', 'NEXT ▶︎ ', part)
        part = re.sub(r'\[\[([^\[\]>]+)(?:>([^\[\]]+))?\]\]', link, part)
        part = re.sub(r'!\[pixiv image (\d+)\]\(pixivimage:\1(?: "[A-Za-z0-9]+")?\)',
                      r'[pixiv image \1](https://www.pixiv.net/artworks/\1)', part)
        part = re.sub(r'\[pixivimage:(\d+)(?::[A-Za-z0-9]+)?\]',
                      r'[pixiv image \1](https://www.pixiv.net/artworks/\1)', part)
        return re.sub(r'\[youtube:([A-Za-z0-9_-]{11})\]', r'[YouTube](https://www.youtube.com/watch?v=\1)', part)
    # Preserve matched backtick code spans, including runs longer than one tick.
    parts = re.split(r'(`+)(.*?)\1', text)
    if len(parts) == 1:
        return prose(text)
    out = prose(parts[0])
    for index in range(1, len(parts), 3):
        out += parts[index] + parts[index + 1] + parts[index] + prose(parts[index + 2])
    return out


def pixiv_to_markdown(source: str) -> str:
    """Convert pixiv's source markup (encyclopedia / novel style) to Markdown.

    ``*`` star headings become ATX headings, ``[[label>target]]`` wiki links and
    ``-``/``--`` wiki lists become links and nested lists, ``^`` row-header tables
    become GFM tables (row headers as bold cells under an empty column header),
    and ``[pixivimage:N]`` / ``[youtube:ID]`` become plain links. Fenced and
    backtick code is left alone, already-normalized Markdown is kept (so it is
    idempotent) and unsupported syntax stays literal. Nothing is downloaded or
    embedded. Use it only for text known to be pixiv source; over 200000
    characters raises ``ValueError``.

    >>> pixiv_to_markdown("*概要\\n[[作品>作品(詳細)]]")
    '# 概要\\n\\n[作品](作品%28詳細%29)'
    """
    if len(source) > 200000:
        raise ValueError('Pixiv source exceeds 200000 characters')
    source = source.replace('\r\n', '\n').replace('\r', '\n').replace('\u2028', '\n').replace('\u2029', '\n').replace('\x00', '\ufffd')
    lines = source.split('\n')
    out: list[str] = []
    fence = None
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            out.append(line)
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            i += 1
            continue
        if match and not (match[1][0] == '`' and '`' in match[2]):
            fence = match[1]
            out.append(line)
            i += 1
            continue
        # Only contiguous pipe rows containing an explicit Pixiv ^ cell marker.
        if line.startswith('|') and line.endswith('|'):
            j = i
            rows = []
            while j < len(lines) and lines[j].startswith('|') and lines[j].endswith('|'):
                rows.append(re.split(r'(?<!\\)\|', lines[j][1:-1]))
                j += 1
            if any(cell.lstrip().startswith('^') for row in rows for cell in row):
                width = max(map(len, rows))
                if out and out[-1].strip():
                    out.append('')
                out.extend(['|' + '|'.join([' '] * width) + '|', '|' + '|'.join([' --- '] * width) + '|'])
                for row in rows:
                    cells = []
                    for cell in row:
                        cell = cell.strip()
                        cells.append('**' + _pixiv_inline(cell[1:]) + '**' if cell.startswith('^') else _pixiv_inline(cell))
                    out.append('| ' + ' | '.join(cells + [''] * (width - len(cells))) + ' |')
                out.append('')
                i = j
                continue
        heading = re.match(r'^(\*{1,6})([^*\s].*?)\s*$', line)
        if heading and heading[1] not in heading[2]:
            if out and out[-1].strip():
                out.append('')
            out.extend(['#' * len(heading[1]) + ' ' + _pixiv_inline(heading[2]), ''])
        else:
            line = re.sub(r'^(-{1,6})(?=[^\s-])', lambda m: '  ' * (len(m[1]) - 1) + '- ', line)
            out.append(_pixiv_inline(line))
        i += 1
    return '\n'.join(out)



def dotenv_to_markdown(content: str, title: str = ".env") -> str:
    """Convert a narrow dotenv subset to canonical structured Markdown.

    Blank lines and full-line comments are ignored; optional export is
    accepted. Variable expansion is never performed. The canonical writer uses
    deterministic double-quoted JSON-compatible string escaping.
    """
    return structured_to_markdown(_dotenv_loads(content), title)


def markdown_to_dotenv(content: str) -> str:
    """Convert canonical structured Markdown to deterministic dotenv text."""
    return _dotenv_dumps(markdown_to_structured(content))


def _toml_key(key: str) -> str:
    if _TOML_BARE_KEY_RE.fullmatch(key):
        return key
    return _structured_json_dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return _structured_json_dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("TOML canonical writer requires finite floats")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        fields = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("TOML mappings require string keys")
            fields.append(f"{_toml_key(key)} = {_toml_value(item)}")
        return "{ " + ", ".join(fields) + " }"
    if value is None:
        raise ValueError("TOML has no null value")
    raise ValueError(f"Unsupported TOML value type: {type(value).__name__}")


def _toml_dumps(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("TOML document root must be a mapping")
    lines = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("TOML mappings require string keys")
        lines.append(f"{_toml_key(key)} = {_toml_value(item)}")
    return "".join(line + "\n" for line in lines)


_TOML_NODE_SCRIPT = r"""
let text = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => (text += c)).on('end', () => {
  let toml;
  try { toml = require('smol-toml'); } catch (e) { process.stderr.write('missing'); process.exit(3); }
  let value;
  try { value = toml.parse(text, {integersAsBigInt: true}); } catch (e) { process.stderr.write('invalid:' + e.message); process.exit(4); }
  // Tag every container as well as every numeric scalar. Literal TOML keys
  // cannot then impersonate metadata, and integral floats remain floats.
  function encode(v) {
    if (typeof v === 'bigint') return ['int', v.toString()];
    if (typeof v === 'number') {
      if (!Number.isFinite(v)) throw new Error('non-finite float');
      return ['float', Object.is(v, -0) ? '-0.0' : v.toString()];
    }
    if (v && typeof v === 'object' && typeof v.toISOString === 'function') throw new Error('datetime');
    if (Array.isArray(v)) return ['array', v.map(encode)];
    if (v && typeof v === 'object') return ['table', Object.entries(v).map(([k, child]) => [k, encode(child)])];
    return ['scalar', v];
  }
  try {
    process.stdout.write(JSON.stringify(encode(value)));
  } catch (e) { process.stderr.write('subset:' + e.message); process.exit(5); }
});
"""
_TOML_READER: list[str | None] = []
_NJS_LAUNCH: Any = None


def _njs_launch_module() -> Any:
    global _NJS_LAUNCH
    if _NJS_LAUNCH is None:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent / "vendor" / "njs_launch.py"
        spec = importlib.util.spec_from_file_location("_vendored_njs_launch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _NJS_LAUNCH = module
    return _NJS_LAUNCH


def _decode_node_process_bytes(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _toml_node_run(
    target: str,
    *args: Any,
    timeout: float = 30,
    **kwargs: Any,
) -> Any:
    """Run vendored ``njs_launch`` for TOML helpers with ``subprocess``-compatible errors."""
    import subprocess

    njs_launch = _njs_launch_module()
    run_kwargs = dict(kwargs)
    node = run_kwargs.pop("node", None)
    env_var = run_kwargs.pop("env_var", njs_launch.DEFAULT_ENV_VAR)
    run_kwargs.pop("probe", None)
    executable = njs_launch.resolve_node(node=node, env_var=env_var)
    command = njs_launch.node_command(
        target, *args, node=executable, probe=False, env_var=env_var
    )
    try:
        return njs_launch.run(
            target,
            *args,
            timeout=timeout,
            node=executable,
            probe=False,
            **run_kwargs,
        )
    except njs_launch.LaunchError as exc:
        if exc.code == "ELAUNCHSTDINCLOSED":
            status = exc.status if exc.status is not None else -1
            if status != 0:
                stderr = _decode_node_process_bytes(exc.stderr)
                raise RuntimeError(f"Node TOML reader failed: {stderr[:200]}") from exc
        if exc.code == "ELAUNCHFAILURE" and "timed out" in str(exc):
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=timeout,
                output=exc.stdout,
                stderr=exc.stderr,
            ) from exc
        raise


def toml_reader() -> str | None:
    """Which TOML reader ``toml_to_markdown`` uses: ``"tomllib"``, ``"node"`` or None.

    Python 3.11+ uses ``tomllib``. Older interpreters fall back to Node 26+ with
    the npm ``smol-toml`` package (resolved via ``NODE_PATH`` or the current
    directory), probed once on first use; None means neither is available.
    """
    if tomllib is not None:
        return "tomllib"
    if not _TOML_READER:
        njs_launch = _njs_launch_module()
        ok = False
        try:
            probe = _toml_node_run("-e", "require('smol-toml')", timeout=30, check=False)
            ok = probe.returncode == 0
        except njs_launch.NodeNotFoundError:
            ok = False
        _TOML_READER.append("node" if ok else None)
    return _TOML_READER[0]


def _toml_node_value(value: Any) -> Any:
    """Decode the tagged Node transport without interpreting user table keys."""
    kind, raw = value
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "table":
        return {key: _toml_node_value(child) for key, child in raw}
    if kind == "array":
        return [_toml_node_value(child) for child in raw]
    if kind == "scalar":
        return raw
    raise ValueError("Unknown Node TOML value type")


def _toml_loads(content: str) -> Any:
    reader = toml_reader()
    if reader == "tomllib":
        try:
            return tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML: {exc}") from exc
    if reader is None:
        raise RuntimeError(
            "toml_to_markdown requires Python 3.11+ (tomllib) or Node 26+ with smol-toml"
            " (npm i --no-save smol-toml)"
        )
    done = _toml_node_run("-e", _TOML_NODE_SCRIPT, input=content, timeout=30, check=False)
    stderr = done.stderr.decode("utf-8", errors="replace")
    stdout = done.stdout.decode("utf-8", errors="replace")
    if done.returncode == 4:
        raise ValueError(f"Invalid TOML: {stderr.removeprefix('invalid:')[:300]}")
    if done.returncode == 5:
        raise ValueError("TOML contains values outside the reversible structured subset")
    if done.returncode:
        raise RuntimeError(f"Node TOML reader failed: {stderr[:200]}")
    return _toml_node_value(json.loads(stdout))


def toml_to_markdown(content: str, title: str = "TOML") -> str:
    """Convert TOML to canonical structured Markdown.

    The reversible subset is the JSON-compatible TOML value space. TOML
    datetime/date/time values and non-finite floats are rejected rather than
    silently stringified. Reads with ``tomllib`` (3.11+) or, on older Python,
    Node 26+ + ``smol-toml`` (see :func:`toml_reader`).
    """
    value = _toml_loads(content)
    try:
        _structured_validate(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "TOML contains values outside the reversible structured subset"
        ) from exc
    return structured_to_markdown(value, title)


def markdown_to_toml(content: str) -> str:
    """Convert canonical structured Markdown to deterministic TOML text."""
    return _toml_dumps(markdown_to_structured(content))


def redis_snapshot_to_markdown(snapshot: Any, title: str = "Redis snapshot") -> str:
    """Document an already obtained Redis/RedisJSON snapshot without connecting."""
    return json_to_markdown(snapshot, title)


def sql_ddl_to_markdown(sql: str, title: str = "SQL schema") -> str:
    """Document a supplied SQL DDL snapshot without parsing or executing it.

    Input line endings are normalized to LF before fencing. The normalized
    trailing-newline count is recorded in a Markdown comment so the companion
    reader restores the LF-normalized snapshot exactly.
    """
    sql = sql.replace("\r\n", "\n").replace("\r", "\n")
    names = _CREATE_TABLE_NAME_RE.findall(sql)
    outline = bullet_list(["Table: " + name for name in names]) if names else ""
    trailing_newlines = len(sql) - len(sql.rstrip("\n"))
    body = sql.rstrip("\n")
    fence = _adaptive_fence(sql, "`")
    fenced = fence + "sql\n" + body + "\n" + fence + "\n"
    marker = "<!-- markdown.py:sql-trailing-newlines=" + str(trailing_newlines) + " -->\n"
    return heading(title) + outline + marker + fenced


def markdown_to_sql_ddl(content: str) -> str:
    """Return the first SQL snapshot without validating or executing it."""
    for block in extract_code_blocks(content):
        if block["language"].lower() == "sql":
            marker = _SQL_TRAILING_NEWLINES_RE.search(content)
            trailing_newlines = int(marker.group(1)) if marker else 0
            return block["code"].rstrip("\n") + "\n" * trailing_newlines
    raise ValueError("No fenced SQL block found")

def key_value_table(
    data: Any,
    key_label: str = "Key",
    value_label: str = "Value",
) -> str:
    """Render a mapping as a two-column Markdown table via ``table``."""
    rows = [[key, value] for key, value in dict(data).items()]
    return table([key_label, value_label], rows)


def _record_to_cells(record: Any) -> list[str]:
    if isinstance(record, dict):
        return [str(v) for v in record.values()]
    if isinstance(record, (list, tuple)):
        return [str(v) for v in record]
    return [str(record)]


def md_table(*headers_and_rows: Any) -> str:
    """``*args`` version of ``table`` — pass values directly, no list needed.

    Dispatches on the first argument:

    - a ``dict`` -> every argument is a record; headers come from the first
      record's keys, and each record's ``.values()`` become that row's cells.
    - a ``list``/``tuple`` -> treated as the header row; the remaining
      arguments are data rows (each a list/tuple of cells, a dict of values,
      or a single scalar rendered as a one-cell row).
    - anything else -> every argument becomes a single-cell row under a
      generic ``value`` header.

    >>> md_table(["name", "val"], ["loss", "0.01"], ["iou", "0.85"])
    '| name | val |\\n| --- | --- |\\n| loss | 0.01 |\\n| iou | 0.85 |\\n'
    >>> md_table({"name": "loss", "val": "0.01"}, {"name": "iou", "val": "0.85"})
    '| name | val |\\n| --- | --- |\\n| loss | 0.01 |\\n| iou | 0.85 |\\n'
    """
    if not headers_and_rows:
        return ""

    first = headers_and_rows[0]
    if isinstance(first, dict):
        headers = [str(k) for k in first.keys()]
        rows = [_record_to_cells(rec) for rec in headers_and_rows]
        return table(headers, rows)

    if isinstance(first, (list, tuple)):
        headers = [str(h) for h in first]
        rows = [_record_to_cells(rec) for rec in headers_and_rows[1:]]
        return table(headers, rows)

    return table(["value"], [[str(v)] for v in headers_and_rows])


def md_kv(*pairs: Any, key_label: str = "Key", value_label: str = "Value") -> str:
    """``*args`` version of ``key_value_table`` — pass ``key, value`` pairs or dicts.

    Alternating positional ``key, value`` arguments and ``dict`` arguments can
    be freely mixed; a trailing key with no value gets an empty string.

    >>> md_kv("mode", "train", "epochs", 10)
    '| Key | Value |\\n| --- | --- |\\n| mode | train |\\n| epochs | 10 |\\n'
    >>> md_kv({"mode": "train"}, "device", "cuda")
    '| Key | Value |\\n| --- | --- |\\n| mode | train |\\n| device | cuda |\\n'
    """
    merged: dict[str, Any] = {}
    it = iter(pairs)
    for item in it:
        if isinstance(item, dict):
            merged.update(item)
        else:
            merged[str(item)] = next(it, "")
    return key_value_table(merged, key_label=key_label, value_label=value_label)


def status_line(ok: bool, msg_ok: str, msg_ng: str) -> str:
    """Build a one-line status message, prefixed with a checkmark or warning."""
    prefix = "✓" if ok else "⚠"
    return f"{prefix} {msg_ok if ok else msg_ng}\n"


def section(title: str, blocks: Any, level: int = 2) -> str:
    """Join a heading with a sequence of pre-rendered Markdown blocks.

    Every builder in this module (``bullet_list``, ``table``, ``code_block``,
    ...) already ends its output in a trailing newline, so back-to-back
    blocks never need anything inserted between them. A plain string used
    directly as a block -- typically hand-written prose with no trailing
    newline of its own -- is the one case that doesn't: joined with nothing
    in between, its text runs directly onto the next block's own leading
    syntax (``- ``, ``# ``, a fence), so e.g. a following bullet list's
    first item silently becomes part of the previous line's plain text
    instead of a list. A newline is inserted only when the accumulated
    output doesn't already end in one, so well-formed blocks are untouched.
    """
    body = heading(title, level=level)
    for block in blocks:
        block = str(block)
        if not block:
            continue
        if not body.endswith("\n"):
            body += "\n"
        body += block
    return body


def wrap_section(name: str, content: str) -> str:
    """Wrap ``content`` in HTML-comment markers so tools/LLMs can find section boundaries."""
    content = content if content.endswith("\n") else content + "\n"
    return f"<!-- BEGIN_SECTION:{name} -->\n{content}<!-- END_SECTION:{name} -->\n"


# ---------------------------------------------------------------------------
# Kramdown IAL (thin heading/paragraph subset — not full Kramdown)
# ---------------------------------------------------------------------------

def _escape_ial_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _parse_attr_list_inner(inner: str) -> dict[str, Any] | None:
    """Parse ``#id .class key=value`` tokens. ``None`` if leftover junk remains."""
    text = inner.strip()
    parsed: dict[str, Any] = {
        "id": None,
        "classes": [],
        "attrs": {},
        "flags": [],
    }
    if not text:
        return parsed
    pos = 0
    length = len(text)
    while pos < length:
        if text[pos].isspace():
            pos += 1
            continue
        match = _ATTR_LIST_TOKEN_RE.match(text, pos)
        if not match:
            return None
        if match.group("id"):
            parsed["id"] = match.group("id")
        elif match.group("cls"):
            parsed["classes"].append(match.group("cls"))
        elif match.group("key"):
            key = match.group("key")
            if match.group("dval") is not None:
                parsed["attrs"][key] = match.group("dval")
            elif match.group("sval") is not None:
                parsed["attrs"][key] = match.group("sval")
            elif match.group("uval") is not None:
                parsed["attrs"][key] = match.group("uval")
            else:
                parsed["flags"].append(key)
        pos = match.end()
    return parsed


def _is_toc_only(parsed: dict[str, Any]) -> bool:
    if parsed["id"] or parsed["classes"] or parsed["attrs"]:
        return False
    flags = [flag.lower() for flag in parsed["flags"]]
    return flags == ["toc"]


def _format_attr_tokens(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
    flags: list[str] | None = None,
) -> list[str]:
    parts: list[str] = []
    if ident:
        parts.append("#" + ident)
    for item in classes:
        parts.append("." + item)
    for key, value in attrs.items():
        parts.append(f'{key}="{_escape_ial_value(value)}"')
    for flag in flags or []:
        parts.append(flag)
    return parts


def _format_ial_line(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
    flags: list[str] | None = None,
) -> str:
    tokens = _format_attr_tokens(ident, classes, attrs, flags)
    if not tokens:
        return "{:}"
    return "{: " + " ".join(tokens) + "}"


def _format_pandoc_attr_list(
    ident: str | None,
    classes: list[str],
    attrs: dict[str, str],
) -> str:
    tokens = _format_attr_tokens(ident, classes, attrs)
    return "{" + " ".join(tokens) + "}"


def _html_attrs_to_pandoc_suffix(attr: dict[str, str]) -> str:
    ident = (attr.get("id") or "").strip()
    if ident and not _IAL_NAME_RE.fullmatch(ident):
        ident = ""
    classes: list[str] = []
    for item in (attr.get("class") or "").split():
        item = item.strip()
        if item and _IAL_NAME_RE.fullmatch(item):
            classes.append(item)
    extra: dict[str, str] = {}
    for key, value in attr.items():
        if key in _HTML_ATTR_SKIP or not value:
            continue
        if not _IAL_KEY_RE.fullmatch(key):
            continue
        extra[key] = value
    if not ident and not classes and not extra:
        return ""
    return " " + _format_pandoc_attr_list(ident or None, classes, extra)


def _normalize_ial_id(ident: str | None) -> str | None:
    if ident is None:
        return None
    ident = str(ident).strip().lstrip("#")
    if not ident:
        return None
    if not _IAL_NAME_RE.fullmatch(ident):
        raise ValueError(f"invalid IAL id {ident!r}")
    return ident


def _normalize_ial_classes(classes: Any) -> list[str]:
    if classes is None:
        return []
    if isinstance(classes, str):
        items = classes.split()
    else:
        items = [str(item) for item in classes]
    out: list[str] = []
    for item in items:
        item = item.strip().lstrip(".")
        if not item:
            continue
        if not _IAL_NAME_RE.fullmatch(item):
            raise ValueError(f"invalid IAL class {item!r}")
        out.append(item)
    return out


def _normalize_ial_attrs(attrs: dict[str, Any]) -> dict[str, str]:
    extra: dict[str, str] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if not _IAL_KEY_RE.fullmatch(str(key)):
            raise ValueError(f"invalid IAL key {key!r}")
        extra[str(key)] = str(value)
    return extra


def ial(
    *,
    id: str | None = None,
    classes: Any = None,
    **attrs: Any,
) -> str:
    """Build a Kramdown Inline Attribute List line.

    Emits ``{: #id .class key="value"}\\n``. Empty input (no id, classes, or
    attrs) returns ``""``. ``classes`` may be a string (whitespace-split) or
    a sequence. A leading ``#`` / ``.`` on id / class names is stripped.

    This is a generator only — ``markdown_to_html`` does not apply IAL as
    HTML attributes.

    >>> ial(id="intro", classes="hero")
    '{: #intro .hero}\\n'
    >>> ial(id="box", classes=["note", "wide"], role="note")
    '{: #box .note .wide role="note"}\\n'
    """
    ident = _normalize_ial_id(id)
    class_list = _normalize_ial_classes(classes)
    extra = _normalize_ial_attrs(attrs)
    if not ident and not class_list and not extra:
        return ""
    return _format_ial_line(ident, class_list, extra) + "\n"


def with_attributes(
    block_md: str,
    *,
    id: str | None = None,
    classes: Any = None,
    **attrs: Any,
) -> str:
    """Append a block IAL after ``block_md``.

    Intended for headings and paragraphs (tiny contract). ``block_md`` is
    left unchanged when no attributes are given.

    >>> with_attributes(heading("Title"), id="intro", classes="hero")
    '# Title\\n{: #intro .hero}\\n'
    """
    suffix = ial(id=id, classes=classes, **attrs)
    if not suffix:
        return str(block_md)
    body = str(block_md)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + suffix


def _join_converted_lines(lines: list[str], original: str) -> str:
    if not lines:
        return "\n" if original.endswith("\n") else ""
    text = "\n".join(lines)
    if original.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    return text


def _is_heading_or_paragraph_line(line: str) -> bool:
    """True when a trailing attr list may attach (headings / paragraphs only)."""
    if _HEADING_RE.match(line):
        return True
    stripped = line.strip()
    if not stripped:
        return False
    if _HR_RE.match(line) or _FENCE_RE.match(line):
        return False
    if _LIST_ITEM_RE.match(line):
        return False
    if line.lstrip().startswith(">"):
        return False
    if _FOOTNOTE_DEF_RE.match(line):
        return False
    if _details_open_summary(line) is not None:
        return False
    if _COLON_CONTAINER_CLOSE_RE.match(line):
        return False
    if _qiita_note_kind(line) is not None or _zenn_message_kind(line) is not None:
        return False
    if stripped.startswith("|"):
        return False
    if stripped.startswith("<!--") or stripped.startswith("{%"):
        return False
    if stripped.startswith("{{"):
        return False
    if stripped == "---":
        return False
    return True


def _rewrite_trailing_attr_list(line: str) -> tuple[str, str] | None:
    """Peel a trailing Pandoc or inline IAL list into ``(body, ial_line)``."""
    if not _is_heading_or_paragraph_line(line):
        return None
    ial_match = _TRAILING_KRAMDOWN_IAL_RE.match(line)
    if ial_match and ial_match.group("body").strip():
        parsed = _parse_attr_list_inner(ial_match.group("inner"))
        if parsed is None or _is_toc_only(parsed):
            return None
        body = ial_match.group("body").rstrip()
        return body, _format_ial_line(
            parsed["id"], parsed["classes"], parsed["attrs"], parsed["flags"]
        )
    pandoc_match = _TRAILING_PANDOC_ATTR_RE.match(line)
    if pandoc_match and pandoc_match.group("body").strip():
        parsed = _parse_attr_list_inner(pandoc_match.group("inner"))
        if parsed is None or _is_toc_only(parsed):
            return None
        if not (
            parsed["id"]
            or parsed["classes"]
            or parsed["attrs"]
            or parsed["flags"]
        ):
            return None
        body = pandoc_match.group("body").rstrip()
        return body, _format_ial_line(
            parsed["id"], parsed["classes"], parsed["attrs"], parsed["flags"]
        )
    return None


def markdown_to_kramdown(content: str) -> str:
    """Rewrite a Markdown subset into Kramdown-leaning Markdown.

    **Rewritten**

    - Pandoc / PHP Markdown Extra trailing braces on headings and
      paragraphs: ``# Title {#id .class key=value}`` becomes a block IAL
      on the next line (``{: #id .class key="value"}``).
    - Inline IAL already on a heading/paragraph (``# Title {: #id}``) is
      moved to a following block IAL and normalized (id, then classes,
      then ``key="value"``).
    - A standalone parseable IAL line is normalized the same way.

    **Left alone**

    - Ordinary Markdown with no attribute list (headings, lists, tables,
      fences, alerts, ``:::details``, footnotes, …).
    - Fenced code (attribute-looking braces inside stay literal).
    - ``{:toc}`` (Kramdown TOC macro), ``{::comment}`` / other extensions,
      span IAL, IAL on lists / quotes / tables, Liquid ``{% %}`` / ``{{ }}``,
      YAML front matter, and unknown brace constructs (safe degrade:
      leave-as-is).

    This is **not** a Kramdown engine. ``markdown_to_html`` does not apply
    IAL as HTML attributes.

    >>> markdown_to_kramdown("# Title {#intro .hero}").splitlines()
    ['# Title', '{: #intro .hero}']
    """
    lines = content.splitlines()
    out: list[str] = []
    in_fence: str | None = None
    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        standalone = _STANDALONE_IAL_RE.match(line)
        if standalone:
            parsed = _parse_attr_list_inner(standalone.group("inner"))
            if parsed is None or _is_toc_only(parsed):
                out.append(line)
            else:
                out.append(
                    _format_ial_line(
                        parsed["id"],
                        parsed["classes"],
                        parsed["attrs"],
                        parsed["flags"],
                    )
                )
            continue
        rewritten = _rewrite_trailing_attr_list(line)
        if rewritten is not None:
            body, ial_line = rewritten
            out.append(body)
            out.append(ial_line)
            continue
        out.append(line)
    return _join_converted_lines(out, content)


def kramdown_to_markdown(content: str) -> str:
    """Strip the supported IAL subset back toward plain Markdown.

    **Rewritten (lossy)**

    - A standalone IAL line ``{: #id .class key="value"}`` after a block
      is removed. Attributes are dropped.
    - A trailing inline IAL on a heading or paragraph
      (``# Title {: #id}``) is stripped.

    **Kept / left alone**

    - The heading or paragraph text itself.
    - Ordinary Markdown, including Pandoc-style ``{#id}`` that was never
      converted to IAL.
    - ``{:toc}``, ``{::comment}`` / ``{::options}`` / ``{::nomarkdown}``,
      unparseable IAL, Liquid ``{% %}`` / ``{{ }}``, YAML front matter,
      and fenced code. Standalone parseable IAL is stripped wherever it
      appears (the line does not record which block it attached to).

    Round-trip ``markdown_to_kramdown`` → ``kramdown_to_markdown`` keeps
    prose and structure; attribute lists are lost. That is intentional.

    >>> kramdown_to_markdown("# Title\\n{: #intro .hero}\\n").splitlines()
    ['# Title']
    """
    lines = content.splitlines()
    out: list[str] = []
    in_fence: str | None = None
    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        standalone = _STANDALONE_IAL_RE.match(line)
        if standalone:
            parsed = _parse_attr_list_inner(standalone.group("inner"))
            if parsed is None or _is_toc_only(parsed):
                out.append(line)
            continue
        ial_match = _TRAILING_KRAMDOWN_IAL_RE.match(line)
        if ial_match:
            body = ial_match.group("body").rstrip()
            parsed = _parse_attr_list_inner(ial_match.group("inner"))
            if (
                parsed is not None
                and not _is_toc_only(parsed)
                and _is_heading_or_paragraph_line(body)
            ):
                out.append(body)
                continue
        out.append(line)
    return _join_converted_lines(out, content)


# ---------------------------------------------------------------------------
# Conservative HTML <-> Markdown (common tags only)
# ---------------------------------------------------------------------------

@dataclass
class HtmlNode:
    """Small normalized HTML tree node; not a browser DOM implementation.

    ``kind`` is ``"root"``, ``"element"``, or ``"text"``. Element nodes use
    lowercase ``tag`` names and sanitized attributes. Unknown tags are retained
    as transparent containers so their safe text/children can degrade without
    passing unsupported markup through.
    """

    kind: str
    tag: str = ""
    attrs: dict[str, str] | None = None
    children: list["HtmlNode"] | None = None
    text: str = ""

    def __post_init__(self) -> None:
        if self.attrs is None:
            self.attrs = {}
        if self.children is None:
            self.children = []


_DOM_SAFE_TAGS = frozenset({
    "a", "aside", "b", "blockquote", "br", "code", "del", "details", "em",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input", "li",
    "ol", "p", "pre", "s", "section", "strong", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})
_DOM_VOID_TAGS = frozenset({"br", "hr", "img", "input"})
_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
_DOM_DROP_TAGS = frozenset({"script", "style"})
_DOM_URL_ATTRS = frozenset({"href", "src"})
_DOM_COMMON_ATTRS = frozenset({
    "alt", "checked", "class", "disabled", "href", "id", "open", "src",
    "title", "type",
})


def _sanitize_url_scheme(value: str) -> str:
    """Normalize and validate a URL for generated HTML attributes.

    C0 controls and DEL are removed before parsing. http/https/mailto and
    ordinary relative URLs are kept; other absolute schemes are rejected.
    An unambiguous host:port reference such as ``example.com:8080/path`` is
    normalized to ``//example.com:8080/path`` so browsers treat it as a
    network-path reference rather than an unknown custom scheme.
    """
    cleaned = "".join(ch for ch in value.strip() if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    if not cleaned:
        return ""
    if cleaned.startswith(("#", "/", "./", "../")):
        return cleaned
    host_port = _HOST_PORT_REFERENCE_RE.fullmatch(cleaned)
    if host_port:
        port = int(host_port.group("port"))
        if 1 <= port <= 65535:
            return "//" + cleaned
        return ""
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        return cleaned
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return cleaned
    return ""


def _dom_sanitize_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for raw_key, raw_value in attrs:
        key = raw_key.lower()
        if key.startswith("on") or key == "style":
            continue
        if key not in _DOM_COMMON_ATTRS and not key.startswith("data-"):
            continue
        value = raw_value or ""
        if key in _DOM_URL_ATTRS:
            value = _sanitize_url_scheme(value)
            if not value:
                continue
        safe[key] = value
    if tag == "input" and safe.get("type", "").lower() != "checkbox":
        return {}
    return safe


class _LightweightDomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("root")
        self.stack: list[HtmlNode] = [self.root]
        self._suppress_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DOM_DROP_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return
        node = HtmlNode(
            "element",
            tag=tag,
            attrs=_dom_sanitize_attrs(tag, attrs) if tag in _DOM_SAFE_TAGS else {},
        )
        assert self.stack[-1].children is not None
        self.stack[-1].children.append(node)
        if tag not in _DOM_VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _DOM_VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DOM_DROP_TAGS:
            if self._suppress_depth:
                self._suppress_depth -= 1
            return
        if self._suppress_depth:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._suppress_depth or not data:
            return
        assert self.stack[-1].children is not None
        self.stack[-1].children.append(HtmlNode("text", text=data))


def parse_html_dom(html: str) -> HtmlNode:
    """Parse HTML into a small sanitized in-memory tree.

    This uses html.parser.HTMLParser, not browser HTML5 tree construction.
    script/style subtrees are dropped, event/style attributes are removed,
    unsafe URL schemes are rejected, and unknown tags become transparent
    containers when serialized.
    """
    parser = _LightweightDomParser()
    parser.feed(html)
    parser.close()
    return parser.root


def html_text_content(node: HtmlNode) -> str:
    """Return decoded descendant text from a lightweight DOM node."""
    if node.kind == "text":
        return node.text
    return "".join(html_text_content(child) for child in (node.children or []))


def find_html_text(html: str, *, tag: str | None = None, attrs: dict[str, str] | None = None) -> str | None:
    """Return textContent-like decoded text for the first matching safe element.

    Matching is depth-first pre-order and attribute values use exact equality.
    No separators are inserted between descendant text nodes. Filters apply to
    the sanitized lightweight DOM; unsupported tags or filtered attributes
    raise ValueError instead of being confused with a missing element.
    """
    wanted_tag = tag.lower() if tag is not None else None
    wanted_attrs = attrs or {}
    normalized_attrs = {key.lower(): value for key, value in wanted_attrs.items()}
    if wanted_tag is not None and wanted_tag not in _DOM_SAFE_TAGS:
        raise ValueError(f"unsupported HTML tag filter: {tag!r}")
    unsupported_attrs = [key for key in wanted_attrs if key.lower() not in _DOM_COMMON_ATTRS and not key.lower().startswith("data-")]
    if unsupported_attrs:
        raise ValueError(f"unsupported HTML attribute filter(s): {', '.join(unsupported_attrs)}")
    if wanted_tag == "input" and normalized_attrs and normalized_attrs.get("type", "").lower() != "checkbox":
        raise ValueError("attribute filters for input require type=checkbox")
    root = parse_html_dom(html)

    def walk(node: HtmlNode) -> HtmlNode | None:
        if node.kind == "element":
            node_attrs = node.attrs or {}
            if ((wanted_tag is None or node.tag == wanted_tag)
                    and all(node_attrs.get(key.lower()) == value for key, value in wanted_attrs.items())):
                return node
        for child in node.children or []:
            found = walk(child)
            if found is not None:
                return found
        return None

    found = walk(root)
    return None if found is None else html_text_content(found)


def dom_to_html(node: HtmlNode) -> str:
    """Serialize a lightweight DOM tree to sanitized HTML."""

    def render(current: HtmlNode) -> str:
        if current.kind == "text":
            return html_module.escape(current.text, quote=False)
        children = "".join(render(child) for child in (current.children or []))
        if current.kind == "root":
            return children
        if current.kind != "element":
            return children
        tag = current.tag.lower()
        if tag not in _DOM_SAFE_TAGS:
            return children
        attrs = current.attrs or {}
        if tag == "a" and not attrs.get("href"):
            return children
        if tag == "img" and not attrs.get("src"):
            return html_module.escape(attrs.get("alt", ""), quote=False)
        attr_text = "".join(
            (f' {key}="{html_module.escape(str(value), quote=True)}"'
             if value != "" else f" {key}")
            for key, value in sorted(attrs.items())
        )
        if tag in _DOM_VOID_TAGS:
            return f"<{tag}{attr_text} />"
        return f"<{tag}{attr_text}>{children}</{tag}>"

    return render(node)


def _dom_details_to_markdown(node: HtmlNode) -> str:
    summary = "Details"
    body_nodes: list[HtmlNode] = []
    for child in node.children or []:
        if child.kind == "element" and child.tag == "summary":
            summary = _html_to_markdown_impl(dom_to_html(child)).strip() or "Details"
        else:
            body_nodes.append(child)
    body_root = HtmlNode("root", children=body_nodes)
    body = _html_to_markdown_impl(dom_to_html(body_root)).strip()
    if body:
        return f":::details {summary}\n{body}\n:::\n"
    return f":::details {summary}\n:::\n"


def dom_to_markdown(node: HtmlNode) -> str:
    """Convert a lightweight DOM tree to normalized Markdown.

    Existing html_to_markdown remains the compatibility engine. details is
    handled explicitly so the DOM path preserves the repository contract.
    """
    if node.kind == "element" and node.tag == "details":
        return _dom_details_to_markdown(node)
    if node.kind == "root":
        parts: list[str] = []
        pending: list[HtmlNode] = []

        def flush_pending() -> None:
            if not pending:
                return
            fragment = HtmlNode("root", children=list(pending))
            converted = _html_to_markdown_impl(dom_to_html(fragment))
            if converted.strip():
                parts.append(converted.strip())
            pending.clear()

        for child in node.children or []:
            if child.kind == "element" and child.tag == "details":
                flush_pending()
                parts.append(_dom_details_to_markdown(child).strip())
            else:
                pending.append(child)
        flush_pending()
        return "\n\n".join(part for part in parts if part).strip() + ("\n" if parts else "")
    return _html_to_markdown_impl(dom_to_html(node))


def markdown_to_dom(content: str) -> HtmlNode:
    """Convert the repository supported Markdown subset to HtmlNode."""
    return parse_html_dom(markdown_to_html(content))


class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._list_stack: list[str] = []
        self._li_index: list[int] = []
        self._in_pre = False
        self._in_code = False
        self._suppress = 0
        self._link_href = ""
        self._link_title = ""
        self._link_open = False
        self._table_depth = 0
        self._table_header: list[str] | None = None
        self._table_rows: list[list[str]] = []
        self._table_row: list[str] | None = None
        self._table_cell: list[str] | None = None
        self._table_row_is_header = False
        self._in_thead = False
        self._block_attr_suffixes: list[str] = []
        self._details_stack: list[dict[str, Any]] = []
        self._blockquote_paragraphs: list[int] = []
        self._skip_next_list_structural_whitespace = False
        self._protected_trailing_space_kind: str | None = None
        self._open_tags: list[str] = []

    def _emit(self, text: str, *, trailing_space_kind: str | None = None) -> None:
        if self._table_cell is not None:
            self._table_cell.append(text)
        elif self._table_depth:
            return
        else:
            self.parts.append(text)
        if text:
            self._protected_trailing_space_kind = trailing_space_kind

    def _trim_structural_boundary(self) -> None:
        """Drop source-formatting whitespace before a block boundary.

        Markdown syntax emitted by this parser can intentionally end in one
        space. Those spaces are tracked by provenance instead of inferred from
        the rendered text, so source text that merely looks similar is still
        normalized normally.
        """
        target = self._table_cell if self._table_cell is not None else self.parts
        if not target or not target[-1]:
            return
        if self._protected_trailing_space_kind is not None:
            return
        target[-1] = target[-1].rstrip(" \t")

    def _flush_cell(self) -> None:
        if self._table_cell is None or self._table_row is None:
            self._table_cell = None
            return
        text = re.sub(r"\s+", " ", "".join(self._table_cell)).strip()
        text = text.replace("|", r"\|")
        self._table_row.append(text)
        self._table_cell = None

    def _flush_row(self) -> None:
        self._flush_cell()
        if self._table_row is None:
            return
        if self._in_thead or self._table_row_is_header:
            if self._table_header is None:
                self._table_header = self._table_row
            else:
                self._table_rows.append(self._table_row)
        else:
            self._table_rows.append(self._table_row)
        self._table_row = None
        self._table_row_is_header = False

    def _table_to_markdown(self) -> str:
        self._flush_row()
        rows = self._table_rows
        header = self._table_header
        if header is None:
            if not rows:
                return ""
            width = max(len(row) for row in rows)
            if width == 0:
                return ""
            header = [""] * width
        width = len(header)
        if width == 0:
            return ""

        def fmt(cells: list[str]) -> str:
            padded = list(cells[:width]) + [""] * (width - min(len(cells), width))
            return "| " + " | ".join(padded) + " |"

        lines = [fmt(header), "| " + " | ".join(["---"] * width) + " |"]
        for row in rows:
            lines.append(fmt(row))
        return "\n\n" + "\n".join(lines) + "\n\n"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        parent_tag = self._open_tags[-1] if self._open_tags else None
        if tag not in _HTML_VOID_TAGS:
            self._open_tags.append(tag)
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in {"script", "style"}:
            self._suppress += 1
            return
        if self._suppress:
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_header = None
                self._table_rows = []
                self._table_row = None
                self._table_cell = None
                self._table_row_is_header = False
                self._in_thead = False
            return
        if self._table_depth > 1 and tag in {
            "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
        }:
            return
        if tag == "thead":
            if self._table_depth == 1:
                self._in_thead = True
            return
        if tag == "tr":
            if self._table_depth == 1:
                self._flush_row()
                self._table_row = []
                self._table_row_is_header = False
            return
        if tag in {"td", "th"}:
            if self._table_depth != 1:
                return
            if self._table_row is None:
                self._table_row = []
                self._table_row_is_header = False
            self._flush_cell()
            self._table_cell = []
            if tag == "th":
                self._table_row_is_header = True
            return
        if tag in {"tbody", "tfoot", "caption", "colgroup", "col"}:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._trim_structural_boundary()
            level = int(tag[1])
            self._emit("\n\n" + ("#" * level) + " ")
            self._block_attr_suffixes.append(_html_attrs_to_pandoc_suffix(attr))
        elif tag == "p":
            self._trim_structural_boundary()
            if parent_tag == "blockquote" and self._blockquote_paragraphs:
                if self._blockquote_paragraphs[-1] > 0:
                    self._emit(
                        "\n>\n> ",
                        trailing_space_kind="blockquote-prefix",
                    )
                self._blockquote_paragraphs[-1] += 1
                self._block_attr_suffixes.append(_html_attrs_to_pandoc_suffix(attr))
            else:
                self._emit("\n\n")
                self._block_attr_suffixes.append(_html_attrs_to_pandoc_suffix(attr))
        elif tag == "br":
            self._emit("  \n")
        elif tag == "hr":
            self._emit("\n\n---\n\n")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag in {"del", "s"}:
            self._emit("~~")
        elif tag == "code" and not self._in_pre:
            self._emit("`")
            self._in_code = True
        elif tag == "pre":
            self._in_pre = True
            self._emit("\n\n```\n")
        elif tag == "a":
            safe_href = _sanitize_url_scheme(attr.get("href", ""))
            if safe_href:
                self._emit("[")
                self._link_href = safe_href
                self._link_title = attr.get("title", "")
                self._link_open = True
            else:
                self._link_href = ""
                self._link_title = ""
                self._link_open = False
        elif tag == "img":
            alt = attr.get("alt", "")
            safe_src = _sanitize_url_scheme(attr.get("src", ""))
            if safe_src:
                self._emit(
                    make_image(
                        alt,
                        safe_src,
                        attr.get("title") or None,
                    )
                )
            else:
                self._emit(alt)
        elif tag == "input":
            if attr.get("type", "").lower() == "checkbox":
                mark = "x" if "checked" in attr else " "
                self._emit(f"[{mark}]")
        elif tag == "blockquote":
            self._blockquote_paragraphs.append(0)
            self._emit(
                "\n\n> ",
                trailing_space_kind="blockquote-prefix",
            )
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                target = self._table_cell if self._table_cell is not None else self.parts
                if (
                    target
                    and target[-1]
                    and self._protected_trailing_space_kind != "list-marker"
                ):
                    target[-1] = target[-1].rstrip()
            self._list_stack.append(tag)
            self._li_index.append(0)
            if len(self._list_stack) == 1:
                self._emit("\n")
        elif tag == "li":
            self._trim_structural_boundary()
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            kind = self._list_stack[-1] if self._list_stack else "ul"
            if kind == "ol":
                self._li_index[-1] += 1
                bullet = f"{self._li_index[-1]}."
            else:
                bullet = "-"
            self._emit(
                f"\n{indent}{bullet} ",
                trailing_space_kind="list-marker",
            )
        elif tag == "details":
            # A fenced :::details block cannot live safely inside one GFM table
            # cell. Preserve the pre-#41 behavior there by flattening the
            # contents instead of reconstructing a multiline container.
            special = self._table_cell is None
            self._details_stack.append(
                {
                    "special": special,
                    "start": len(self.parts) if special else None,
                    "summary_start": None,
                    "summary": "Details",
                }
            )
        elif tag == "summary" and self._details_stack:
            current = self._details_stack[-1]
            if current.get("special") and parent_tag == "details":
                current["summary_start"] = len(self.parts)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        implicitly_closed: list[str] = []
        matched_open_tag = False
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index] == tag:
                matched_open_tag = True
                implicitly_closed = self._open_tags[index + 1:]
                del self._open_tags[index:]
                break

        # html.parser does not synthesize end-tag callbacks for malformed
        # descendants. The lightweight DOM path nevertheless serializes those
        # descendants as balanced HTML before conversion. Mirror that narrow
        # recovery for inline Markdown delimiters when an ancestor closes.
        for implicit_tag in reversed(implicitly_closed):
            if implicit_tag in {"strong", "b"}:
                self._emit("**")
            elif implicit_tag in {"em", "i"}:
                self._emit("*")
            elif implicit_tag in {"del", "s"}:
                self._emit("~~")
            elif implicit_tag == "code" and self._in_code and not self._in_pre:
                self._emit("`")
                self._in_code = False
        if tag in {"script", "style"}:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress:
            return
        if tag == "table":
            if self._table_depth == 1:
                markdown = self._table_to_markdown()
                self._table_depth = 0
                self._table_cell = None
                self._table_row = None
                if markdown:
                    self.parts.append(markdown)
            elif self._table_depth > 1:
                self._table_depth -= 1
            return
        if self._table_depth > 1 and tag in {
            "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
        }:
            return
        if tag == "thead":
            if self._table_depth == 1:
                self._flush_row()
                self._in_thead = False
            return
        if tag == "tr":
            if self._table_depth == 1:
                self._flush_row()
            return
        if tag in {"td", "th"}:
            if self._table_depth == 1:
                self._flush_cell()
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._trim_structural_boundary()
            suffix = (
                self._block_attr_suffixes.pop()
                if self._block_attr_suffixes
                else ""
            )
            if suffix:
                self._emit(suffix)
            if tag == "p" and self._blockquote_paragraphs:
                parent_tag = self._open_tags[-1] if self._open_tags else None
                if parent_tag == "blockquote":
                    return
            self._emit("\n\n")
        elif tag in {"strong", "b"} and matched_open_tag:
            self._emit("**")
        elif tag in {"em", "i"} and matched_open_tag:
            self._emit("*")
        elif tag in {"del", "s"} and matched_open_tag:
            self._emit("~~")
        elif tag == "code" and matched_open_tag and self._in_code and not self._in_pre:
            self._emit("`")
            self._in_code = False
        elif tag == "pre":
            # A source terminal LF already separates the closing fence. Adding
            # another makes it a literal extra blank line when rendered again.
            target = self._table_cell if self._table_cell is not None else self.parts
            separator = "" if target and target[-1].endswith("\n") else "\n"
            self._emit(separator + "```\n\n")
            self._in_pre = False
        elif tag == "a" and self._link_open:
            if self._link_title:
                self._emit(f']({self._link_href} "{self._link_title}")')
            else:
                self._emit(f"]({self._link_href})")
            self._link_open = False
        elif tag == "summary" and self._details_stack:
            current = self._details_stack[-1]
            summary_start = current.get("summary_start")
            if current.get("special") and isinstance(summary_start, int):
                summary = "".join(self.parts[summary_start:]).strip()
                del self.parts[summary_start:]
                current["summary"] = summary or "Details"
                current["summary_start"] = None
        elif tag == "details" and self._details_stack:
            current = self._details_stack.pop()
            if current.get("special"):
                start = current.get("start")
                if isinstance(start, int):
                    body = "".join(self.parts[start:])
                    del self.parts[start:]
                    body = _collapse_blank_runs(body, 1).strip()
                    summary = str(current.get("summary") or "Details")
                    if body:
                        self._emit(f"\n\n:::details {summary}\n{body}\n:::\n\n")
                    else:
                        self._emit(f"\n\n:::details {summary}\n:::\n\n")
        elif tag == "blockquote":
            if self._blockquote_paragraphs:
                self._blockquote_paragraphs.pop()
            self._emit("\n\n")
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if self._li_index:
                self._li_index.pop()
            if self._list_stack:
                self._skip_next_list_structural_whitespace = True
            else:
                self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        if self._table_depth and self._table_cell is None:
            return
        if self._skip_next_list_structural_whitespace:
            self._skip_next_list_structural_whitespace = False
            if not data.strip():
                return
        if (
            not data.strip()
            and self._open_tags
            and self._open_tags[-1] in {"blockquote", "ul", "ol"}
        ):
            return
        if self._in_pre or self._in_code:
            self._emit(data)
        else:
            collapsed = re.sub(r"\s+", " ", data)
            # HTML comments and transparent/ignored markup can split what is
            # semantically one whitespace run into multiple handle_data()
            # callbacks. Coalesce that boundary so legacy conversion matches
            # the DOM path, which removes such nodes before re-serialization.
            target = self._table_cell if self._table_cell is not None else self.parts
            if (
                collapsed.startswith(" ")
                and target
                and target[-1]
                and target[-1][-1].isspace()
            ):
                collapsed = collapsed[1:]
            if collapsed:
                self._emit(collapsed)

    def output(self) -> str:
        text = "".join(self.parts)
        # Structural spacing may collapse; literal blank lines inside a code
        # fence must survive HTML -> Markdown -> HTML unchanged.
        text = _collapse_blank_runs(text, 1)
        normalized = text.strip()
        return normalized + "\n" if normalized else ""


def _html_to_markdown_impl(html: str) -> str:
    """Conservatively convert common HTML tags to Markdown.

    Simple ``<table>`` trees become GFM pipe tables (``| a | b |`` plus a
    ``| --- |`` separator). ``<thead>`` / ``<th>`` rows are headers;
    headerless tables use an empty header row so every ``<td>`` row stays
    data (GFM cannot represent a table with no header). ``|`` inside cells
    is escaped as ``\\|``. Nested tables flatten to cell text; ``colspan`` /
    ``rowspan`` and ``<caption>`` are ignored.

    ``<del>`` (and ``<s>``) become ``~~text~~``. A checkbox
    ``<input type="checkbox">`` (optional ``checked``) becomes ``[ ]`` /
    ``[x]``, which pairs with ``<li>`` as ``- [ ]`` / ``- [x]``.

    ``id`` / ``class`` (and other simple key=value attrs except ``style``)
    on ``<h1>``–``<h6>`` and ``<p>`` become a trailing Pandoc-style
    ``{#id .class key="value"}`` so :func:`markdown_to_kramdown` can attach
    a Kramdown IAL. This is not applied to tables, lists, or other tags.
    """
    parser = _HTMLToMarkdownParser()
    parser.feed(html)
    parser.close()
    return parser.output()



def markdown_to_web_ui_v1(
    content: str,
    *,
    title: str | None = None,
    theme: Literal["modern", "github-like"] = "modern",
) -> str:
    """Render Markdown inside the stable web-ui HTML contract v1 surface.

    This emits semantic HTML only. It does not fetch or embed web-ui CSS and
    therefore preserves this module's single-file, standard-library-only
    runtime contract. Consumers may load their pinned web-ui assets separately.

    ``theme`` is case-sensitive and limited to the currently frozen v1 theme values.
    ``title`` is escaped as text. Markdown body conversion follows
    ``markdown_to_html`` and its documented supported subset.
    """
    if theme not in {"modern", "github-like"}:
        raise ValueError("theme must be 'modern' or 'github-like'")
    body = markdown_to_html(content)
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>',
        f'<body data-ui-theme="{theme}">',
        '<main class="ui-page">',
    ]
    # The title is optional by contract; the body wrapper is always emitted.
    if title is not None:
        parts.append(f'<h1 class="ui-title">{html_module.escape(title)}</h1>')
    parts.extend([
        '<section class="ui-panel">',
        body,
        "</section>",
        "</main>",
        "</body>",
        "</html>",
    ])
    return "\n".join(parts)

def html_to_markdown(html: str) -> str:
    """Conservatively convert common HTML tags to Markdown.

    This public compatibility entry point intentionally delegates to the
    legacy parser engine. Keeping the engine separate lets DOM conversion
    reuse the same implementation without creating a future recursion loop.
    """
    return _html_to_markdown_impl(html)


def _escape_html_text(text: str) -> str:
    return html_module.escape(text, quote=False)


def _angle_autolink_html(url: str) -> str:
    """Render a safe stashed angle autolink; unsafe URLs degrade to text."""
    safe_url = _sanitize_url_scheme(url)
    text = html_module.escape(url)
    if not safe_url:
        return text
    href = html_module.escape(safe_url, quote=True)
    return f'<a href="{href}">{text}</a>'


def _parse_task_item(item: str) -> tuple[bool, str] | None:
    """Return ``(checked, rest)`` for a GFM task marker, else ``None``.

    Only the item body is inspected (list marker already stripped).
    ``[ ]`` is unchecked; ``[x]`` / ``[X]`` are checked. A following
    description needs whitespace after ``]``; ``- [x]done`` is not a task.
    """
    match = _TASK_ITEM_RE.match(item)
    if not match:
        return None
    checked = match.group(1) in "xX"
    rest = match.group(2) or ""
    return checked, rest


def _task_checkbox_html(checked: bool) -> str:
    if checked:
        return '<input type="checkbox" disabled checked />'
    return '<input type="checkbox" disabled />'


def _unquote_blockquote_line(line: str) -> str | None:
    match = _BLOCKQUOTE_PREFIX_RE.match(line)
    if not match:
        return None
    return match.group(2)


def _blockquote_alert_info(line: str) -> dict[str, str] | None:
    match = _BLOCKQUOTE_ALERT_RE.match(line)
    if not match:
        return None
    raw_kind = match.group(1)
    fold = match.group(2) or ""
    title = (match.group(3) or "").strip()
    github_shaped = (
        raw_kind in _GITHUB_ALERT_KIND_SET and not fold and not title
    )
    if github_shaped:
        return {
            "flavor": "github",
            "kind": raw_kind,
            "title": raw_kind,
            "fold": "",
        }
    return {
        "flavor": "obsidian",
        "kind": raw_kind.lower(),
        "title": title,
        "fold": fold,
    }


def _qiita_note_kind(line: str) -> str | None:
    match = _QIITA_NOTE_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "info").lower()


def _zenn_message_kind(line: str) -> str | None:
    match = _ZENN_MESSAGE_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "message").lower()


def _is_alert_block_open(line: str) -> bool:
    return bool(
        _blockquote_alert_info(line)
        or _qiita_note_kind(line) is not None
        or _zenn_message_kind(line) is not None
        or _details_open_summary(line) is not None
    )


def _details_open_summary(line: str) -> str | None:
    """Return the summary text if ``line`` opens a ``:::details`` block."""
    match = _DETAILS_OPEN_RE.match(line)
    if not match:
        return None
    return (match.group(1) or "").strip()


def _footnote_slug(ident: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ident).strip("-")
    return slug or "note"


def _collect_footnote_definitions(lines: list[str]) -> tuple[list[str], dict[str, str]]:
    """Strip ``[^id]:`` definitions (except inside fences); first id wins.

    Indented (2+ spaces or a tab) lines immediately after a definition are
    continuations. A blank line or a non-indented line ends the note.
    """
    defs: dict[str, str] = {}
    kept: list[str] = []
    i = 0
    in_fence: str | None = None
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)
            if in_fence is None:
                in_fence = mark
            elif line.startswith(in_fence):
                in_fence = None
            kept.append(line)
            i += 1
            continue
        if in_fence:
            kept.append(line)
            i += 1
            continue
        match = _FOOTNOTE_DEF_RE.match(line)
        if match:
            ident = match.group(1)
            parts = [match.group(2)]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if _FOOTNOTE_DEF_RE.match(nxt):
                    break
                cont = _FOOTNOTE_CONT_RE.match(nxt)
                if not cont:
                    break
                parts.append(cont.group(1).strip())
                i += 1
            if ident not in defs:
                defs[ident] = " ".join(p for p in parts if p).strip()
            continue
        kept.append(line)
        i += 1
    return kept, defs


def _footnote_ref_html(ident: str, number: int, *, with_id: bool) -> str:
    slug = html_module.escape(_footnote_slug(ident), quote=True)
    label = html_module.escape(str(number))
    if with_id:
        return (
            f'<sup class="footnote-ref">'
            f'<a href="#fn-{slug}" id="fnref-{slug}">{label}</a>'
            f"</sup>"
        )
    return (
        f'<sup class="footnote-ref">'
        f'<a href="#fn-{slug}">{label}</a>'
        f"</sup>"
    )


def _footnotes_section_html(
    order: list[str],
    defs: dict[str, str],
    render_inline,
) -> str:
    items: list[str] = ['<section class="footnotes">', "<ol>"]
    for ident in order:
        slug = html_module.escape(_footnote_slug(ident), quote=True)
        note = render_inline(defs.get(ident, ""))
        items.append(
            f'<li id="fn-{slug}">{note}'
            f' <a href="#fnref-{slug}" class="footnote-backref">↩</a></li>'
        )
    items.append("</ol>")
    items.append("</section>")
    return "\n".join(items)


def _consume_details(
    lines: list[str],
    start: int,
    summary: str,
    render_inline,
) -> tuple[str, int]:
    """Render paragraphs and fenced code; only an unfenced ::: closes details.

    Inner headings/lists/nested details retain the existing paragraph contract.
    Code rendering and fence recognition reuse the main renderer and scanner.
    """
    i = start + 1
    parts = ["<details>", f"<summary>{render_inline(summary)}</summary>"]
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(line.strip() for line in paragraph)
            parts.append(f"<p>{render_inline(text)}</p>")
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if _COLON_CONTAINER_CLOSE_RE.match(line):
            i += 1
            break
        active, opened, _ = _advance_fence(None, line)
        if opened:
            flush_paragraph()
            code_lines = [line]
            i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                active, _, closed = _advance_fence(active, lines[i])
                i += 1
                if closed:
                    break
            # Only a fenced segment is delegated, never the prose/container.
            parts.append(markdown_to_html("\n".join(code_lines)).rstrip("\n"))
            continue
        if not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
        i += 1

    flush_paragraph()
    parts.append("</details>")
    return "\n".join(parts), i


def _alert_aside_html(
    *,
    flavor: str,
    kind: str,
    title: str,
    body_markdown: str,
    fold: str = "",
    render_inline,
) -> str:
    kind_slug = kind.lower()
    kind_token = html_module.escape(kind.upper(), quote=True)
    flavor_token = html_module.escape(flavor, quote=True)
    css_kind = html_module.escape(kind_slug, quote=True)
    attrs = [
        f'class="markdown-alert markdown-alert-{css_kind}"',
        f'data-alert="{kind_token}"',
        f'data-alert-flavor="{flavor_token}"',
    ]
    if fold == "+":
        attrs.append('data-alert-fold="open"')
    elif fold == "-":
        attrs.append('data-alert-fold="closed"')
    display_title = title if title else kind.upper()
    parts = [
        f"<aside {' '.join(attrs)}>",
        f'<p class="markdown-alert-title">{render_inline(display_title)}</p>',
    ]
    body = body_markdown.strip("\n")
    if body.strip():
        body_html = markdown_to_html(body).rstrip("\n")
        if body_html:
            parts.append(body_html)
    parts.append("</aside>")
    return "\n".join(parts)


def _consume_blockquote_alert(
    lines: list[str],
    start: int,
    info: dict[str, str],
    render_inline,
) -> tuple[str, int]:
    i = start + 1
    body_lines: list[str] = []
    while i < len(lines):
        unquoted = _unquote_blockquote_line(lines[i])
        if unquoted is None:
            break
        body_lines.append(unquoted)
        i += 1
    html = _alert_aside_html(
        flavor=info["flavor"],
        kind=info["kind"],
        title=info["title"],
        body_markdown="\n".join(body_lines),
        fold=info["fold"],
        render_inline=render_inline,
    )
    return html, i


def _consume_colon_alert(
    lines: list[str],
    start: int,
    *,
    flavor: str,
    kind: str,
    render_inline,
) -> tuple[str, int]:
    i = start + 1
    body_lines: list[str] = []
    while i < len(lines) and not _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        body_lines.append(lines[i])
        i += 1
    if i < len(lines) and _COLON_CONTAINER_CLOSE_RE.match(lines[i]):
        i += 1
    title = kind.upper()
    html = _alert_aside_html(
        flavor=flavor,
        kind=kind,
        title=title,
        body_markdown="\n".join(body_lines),
        render_inline=render_inline,
    )
    return html, i


def _split_table_row(line: str) -> list[str]:
    """Split a GFM pipe row into cells.

    Leading/trailing pipes are optional. A ``\\|`` sequence stays one cell
    (the backslash is dropped). This is the same split ``table()`` output
    round-trips through; it is not a full GFM cell lexer.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    before_last = stripped[:-1] if stripped.endswith("|") else stripped
    trailing_slashes = len(before_last) - len(before_last.rstrip("\\"))
    if stripped.endswith("|") and trailing_slashes % 2 == 0:
        stripped = stripped[:-1]
    cells: list[str] = []
    cell: list[str] = []
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] in {"\\", "|"}:
            cell.append(stripped[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
        index += 1
    cells.append("".join(cell).strip())
    return cells


def _looks_like_table_row(line: str) -> bool:
    return "|" in line.strip()


def _is_table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(_TABLE_SEP_CELL_RE.match(cell.replace(" ", "")) for cell in cells)


def _is_table_start(lines: list[str], index: int) -> bool:
    """True when ``lines[index]`` is a header row followed by a ``| --- |`` delimiter.

    List markers, headings, fences, thematic breaks, and ``>`` quotes are
    not treated as headers, so a malformed ``- a | b`` line stays a list.
    A pipe row with no delimiter after it stays a paragraph (safe degrade).
    """
    if index + 1 >= len(lines):
        return False
    header = lines[index]
    if not header.strip() or not _looks_like_table_row(header):
        return False
    if (
        _HEADING_RE.match(header)
        or _FENCE_RE.match(header)
        or _HR_RE.match(header)
        or _unquote_blockquote_line(header) is not None
        or _LIST_ITEM_RE.match(header)
        or _is_alert_block_open(header)
    ):
        return False
    return _is_table_separator(lines[index + 1])


def _table_html(
    headers: list[str],
    rows: list[list[str]],
    render_inline,
) -> str:
    width = len(headers)

    def row_html(tag: str, cells: list[str]) -> str:
        padded = list(cells[:width]) + [""] * (width - min(len(cells), width))
        inner = "".join(f"<{tag}>{render_inline(cell)}</{tag}>" for cell in padded)
        return f"<tr>{inner}</tr>"

    parts = ["<table>", "<thead>", row_html("th", headers), "</thead>", "<tbody>"]
    for row in rows:
        parts.append(row_html("td", row))
    parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def _consume_table(
    lines: list[str],
    start: int,
    render_inline,
) -> tuple[str, int]:
    headers = _split_table_row(lines[start])
    i = start + 2
    rows: list[list[str]] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip() or not _looks_like_table_row(line):
            break
        if (
            _HEADING_RE.match(line)
            or _FENCE_RE.match(line)
            or _HR_RE.match(line)
            or _unquote_blockquote_line(line) is not None
            or _is_alert_block_open(line)
        ):
            break
        rows.append(_split_table_row(line))
        i += 1
    return _table_html(headers, rows, render_inline), i


def _consume_blockquote(
    lines: list[str],
    start: int,
    render_inline,
) -> tuple[str, int]:
    """Collect consecutive ``>`` lines into a ``<blockquote>``.

    Blank ``>`` / ``> `` lines split paragraphs. Inner ATX headings, lists,
    nested quotes, and tables are **not** re-parsed as blocks -- they stay
    paragraph text -- so GitLab ``>>>`` openers cannot collapse into alerts.
    A later ``> [!TYPE]`` line ends this quote so the alert handler can win.
    """
    i = start
    body_lines: list[str] = []
    while i < len(lines):
        unquoted = _unquote_blockquote_line(lines[i])
        if unquoted is None:
            break
        if i > start and _blockquote_alert_info(lines[i]):
            break
        body_lines.append(unquoted)
        i += 1

    paragraphs: list[list[str]] = [[]]
    for raw in body_lines:
        if not raw.strip():
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(raw)

    parts = ["<blockquote>"]
    for para in paragraphs:
        if not para:
            continue
        text = " ".join(s.strip() for s in para)
        parts.append(f"<p>{render_inline(text)}</p>")
    parts.append("</blockquote>")
    return "\n".join(parts), i


def markdown_to_html(content: str, *, raw_html: bool = False, attr_list: bool = False) -> str:
    """Conservatively convert a Markdown *subset* to HTML.

    Opt-in modes for trusted input (both off by default; the default output is
    unchanged and escapes every ``<``):

    - ``raw_html=True``: block-level HTML passes through as written (CommonMark
      HTML blocks: ``<script>`` / ``<style>`` / ``<pre>`` / ``<textarea>`` up to
      their closing tag, other block tags such as ``<div>`` / ``<p>`` /
      ``<details>`` / ``<summary>`` up to the next blank line). An opening tag
      alone on its line with ``markdown="1"`` keeps the tag (without that
      attribute) and renders its contents, up to the matching closing tag at
      the start of a line, as Markdown in the same mode (Python-Markdown's
      ``md_in_html``). Entity references (``&lt;``, ``&#60;``, ``&#x3c;``) in
      text are kept as entities, as CommonMark does; inside ``<code>`` and
      ``<pre>`` they stay literal text.
    - ``attr_list=True``: a Kramdown block IAL line (``{: #id .class
      key="value"}``) right after a paragraph, heading, table or list adds
      those attributes to its first tag, and an ATX heading may end with
      ``{#id .class}`` or ``{: #id}`` (Python-Markdown's ``attr_list`` for
      blocks and headings). Lines that do not parse stay text.

    Footnotes are numbered per Markdown run between HTML blocks in these modes.

    Handles ATX headings, fenced code, paragraphs, inline code, bold/italic,
    strikethrough (``~~text~~`` → ``<del>``), links, images, angle-bracket
    http(s) autolinks, thematic breaks, simple indentation-based nested
    bullet/numbered lists, nested GFM task lists, ordinary ``>`` blockquotes, simple GFM pipe tables,
    GitHub / Qiita / Zenn / Obsidian alerts, Zenn-style ``:::details``
    collapsible sections, and a small GFM/Pandoc-like footnote subset.

    Alerts render to a shared ``<aside>`` shape. Pair with
    ``default_stylesheet()`` (or ``alert_stylesheet()``) if you want CSS::

        <style>{default_stylesheet()}</style>
        <aside class="markdown-alert markdown-alert-note"
               data-alert="NOTE" data-alert-flavor="github">
        <p class="markdown-alert-title">NOTE</p>
        <p>…body…</p>
        </aside>

    Classification:

    - ``> [!NOTE]`` (uppercase GitHub kinds, no same-line title, no fold)
      is ``data-alert-flavor="github"``.
    - Other ``> [!type]`` markers (lowercase, extra types, optional title,
      ``+``/``-`` fold) are ``obsidian``. GitLab's lowercase five-kind
      form therefore parses as Obsidian; its ``>>>`` multiline alerts are
      not recognized.
    - ``:::note`` / ``:::note info|warn|alert`` … ``:::`` is ``qiita``.
    - ``:::message`` / ``:::message alert`` … ``:::`` is ``zenn``.
    - ``:::details Summary`` … ``:::`` becomes ``<details><summary>``.
      Nested inlines and fenced code are parsed. Other nested blocks stay
      paragraph text. Only an unfenced ``:::`` closes the container. Raw
      HTML ``<details>`` stays escaped.
    - Ordinary ``>`` lines that are not an alert opener become
      ``<blockquote>`` (multi-line; blank ``>`` lines split paragraphs).
      Nested block constructs inside the quote are not parsed.
    - A header row followed by a ``| --- |`` delimiter row becomes
      ``<table>``. Cell text is escaped, then the same inline renderer
      used for paragraphs is applied (bold / italic / code / links / images
      / strikethrough / angle autolinks / footnotes). Pipe rows without
      that delimiter stay paragraphs.
    - ``~~text~~`` becomes ``<del>text</del>``. Unmatched ``~~`` stays
      literal; ``~~a~~b~~`` takes the first pair. Nested bold/italic
      inside or around a pair is applied.
    - Unordered ``-`` / ``*`` / ``+`` items whose body is ``[ ]`` /
      ``[x]`` / ``[X]`` (optional description after whitespace) become
      ``<li>`` with ``<input type="checkbox" disabled>`` (``checked``
      for x/X). The box is not interactive. Task items and ordinary
      bullets in the same list stay one ``<ul>`` with mixed ``<li>``
      shapes. ``1. [ ]`` stays ordinary ordered-list text.
    - ``<https://...>`` / ``<http://...>`` become ``<a href="...">``.
      Bare URLs, ``mailto:``, and ordinary ``<tag>`` (including
      ``<script>``) are not autolinks; non-URL angle brackets stay
      escaped. ``[text](url)`` links still win when both forms appear.
    - ``[^id]`` plus a ``[^id]: note`` definition become a superscript
      link and a trailing ``<section class="footnotes">``. Undefined
      refs stay literal. Duplicate ids: first definition wins. Unused
      defs are dropped. Only indented (2+ spaces / tab) continuation
      is kept; unindented wrapping is a new paragraph.

    Unknown GitHub/Qiita/Zenn kinds are not generated (``alert()`` raises);
    unknown Obsidian types still parse as Obsidian callouts (matching
    Obsidian's custom-type support).
    """
    if raw_html or attr_list:
        return _markdown_to_html_blocks(content, raw_html=raw_html, attr_list=attr_list)
    # Sanitize before all parsing paths, including fenced code: html.escape
    # leaves NUL intact, and input must not forge internal NUL placeholders.
    content = content.replace("\x00", "\ufffd")
    source_lines = content.splitlines()
    lines, footnote_defs = _collect_footnote_definitions(source_lines)
    footnote_order: list[str] = []
    footnote_ref_seen: set[str] = set()
    out: list[str] = []
    i = 0
    in_list: str | None = None

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def render_inline(text: str) -> str:
        placeholders: list[str] = []

        def stash(snippet: str) -> str:
            placeholders.append(snippet)
            return f"\x00PH{len(placeholders) - 1}\x00"

        def replace_footnote(match: re.Match[str]) -> str:
            ident = match.group(1)
            if ident not in footnote_defs:
                return match.group(0)
            if ident not in footnote_order:
                footnote_order.append(ident)
            number = footnote_order.index(ident) + 1
            with_id = ident not in footnote_ref_seen
            if with_id:
                footnote_ref_seen.add(ident)
            return stash(_footnote_ref_html(ident, number, with_id=with_id))

        text = _INLINE_IMAGE_RE.sub(
            lambda m: stash(markdown_image_to_html(m.group(0))),
            text,
        )
        text = _INLINE_LINK_RE.sub(
            lambda m: stash(markdown_link_to_html(m.group(0))),
            text,
        )
        text = re.sub(
            r"`([^`]+)`",
            lambda m: stash(f"<code>{_escape_html_text(m.group(1))}</code>"),
            text,
        )
        text = _ANGLE_URL_RE.sub(
            lambda m: stash(_angle_autolink_html(m.group(1))),
            text,
        )
        text = _FOOTNOTE_REF_RE.sub(replace_footnote, text)
        text = _escape_html_text(text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
        text = _STRIKETHROUGH_RE.sub(r"<del>\1</del>", text)
        for index, snippet in enumerate(placeholders):
            text = text.replace(f"\x00PH{index}\x00", snippet)
        return text

    def consume_list(start: int) -> tuple[str, int]:
        """Consume one contiguous Markdown list, preserving simple nesting.

        Nesting is indentation-based and deliberately small: list items may
        contain child ul/ol lists, but arbitrary block content inside an item
        remains out of scope.
        """

        def match_list_line(index: int):
            if index >= len(lines):
                return None
            return re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", lines[index])

        def kind_for(marker: str) -> str:
            return "ol" if marker.endswith(".") and marker[:-1].isdigit() else "ul"

        def render_item(kind: str, item: str) -> str:
            task = _parse_task_item(item) if kind == "ul" else None
            if task is None:
                return render_inline(item)
            checked, rest = task
            box = _task_checkbox_html(checked)
            body = render_inline(rest)
            return f"{box} {body}" if body else box

        def consume_level(index: int, indent: int, kind: str) -> tuple[list[str], int]:
            parts = [f"<{kind}>"]
            while index < len(lines):
                match = match_list_line(index)
                if match is None:
                    break
                current_indent = len(match.group(1).expandtabs(4))
                current_kind = kind_for(match.group(2))
                if current_indent < indent:
                    break
                if current_indent != indent or current_kind != kind:
                    break

                item = match.group(3)
                index += 1
                children: list[str] = []
                while index < len(lines):
                    child = match_list_line(index)
                    if child is None:
                        break
                    child_indent = len(child.group(1).expandtabs(4))
                    if child_indent <= indent:
                        break
                    child_kind = kind_for(child.group(2))
                    child_parts, index = consume_level(index, child_indent, child_kind)
                    children.extend(child_parts)

                body = render_item(kind, item)
                if children:
                    parts.append(f"<li>{body}")
                    parts.extend(children)
                    parts.append("</li>")
                else:
                    parts.append(f"<li>{body}</li>")

            parts.append(f"</{kind}>")
            return parts, index

        first = match_list_line(start)
        if first is None:
            return "", start
        indent = len(first.group(1).expandtabs(4))
        kind = kind_for(first.group(2))
        parts, end = consume_level(start, indent, kind)
        return "\n".join(parts), end

    def paragraph_interrupt(index: int) -> bool:
        line = lines[index]
        if not line.strip():
            return True
        if (
            _HEADING_RE.match(line)
            or _FENCE_RE.match(line)
            or _HR_RE.match(line)
            or _is_alert_block_open(line)
            or _unquote_blockquote_line(line) is not None
            or _is_table_start(lines, index)
        ):
            return True
        if re.match(r"^(\s*)[-*+]\s+", line) or re.match(r"^(\s*)\d+\.\s+", line):
            return True
        return False

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            close_list()
            fence_mark = fence.group(1)
            info = fence.group(2).strip()
            lang = info.split()[0] if info else ""
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(fence_mark):
                body.append(lines[i])
                i += 1
            code = _escape_html_text("\n".join(body))
            cls = f' class="language-{html_module.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}\n</code></pre>")
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            close_list()
            level = len(heading.group(1))
            title = render_inline(heading.group(2).strip())
            out.append(f"<h{level}>{title}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line):
            close_list()
            out.append("<hr />")
            i += 1
            continue

        alert_info = _blockquote_alert_info(line)
        if alert_info:
            close_list()
            html, i = _consume_blockquote_alert(lines, i, alert_info, render_inline)
            out.append(html)
            continue

        qiita_kind = _qiita_note_kind(line)
        if qiita_kind is not None:
            close_list()
            html, i = _consume_colon_alert(
                lines,
                i,
                flavor="qiita",
                kind=qiita_kind,
                render_inline=render_inline,
            )
            out.append(html)
            continue

        zenn_kind = _zenn_message_kind(line)
        if zenn_kind is not None:
            close_list()
            html, i = _consume_colon_alert(
                lines,
                i,
                flavor="zenn",
                kind=zenn_kind,
                render_inline=render_inline,
            )
            out.append(html)
            continue

        details_summary = _details_open_summary(line)
        if details_summary is not None:
            close_list()
            html, i = _consume_details(lines, i, details_summary, render_inline)
            out.append(html)
            continue

        if _unquote_blockquote_line(line) is not None:
            close_list()
            html, i = _consume_blockquote(lines, i, render_inline)
            out.append(html)
            continue

        if _is_table_start(lines, i):
            close_list()
            html, i = _consume_table(lines, i, render_inline)
            out.append(html)
            continue

        ul = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        ol = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ul or ol:
            close_list()
            html, i = consume_list(i)
            out.append(html)
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        close_list()
        para = [line]
        i += 1
        while i < len(lines) and not paragraph_interrupt(i):
            para.append(lines[i])
            i += 1
        rendered_para: list[str] = []
        for index, raw in enumerate(para):
            rendered_para.append(render_inline(raw.strip()))
            if index + 1 < len(para):
                rendered_para.append("<br />" if raw.endswith("  ") else " ")
        out.append(f"<p>{''.join(rendered_para)}</p>")

    close_list()
    if footnote_order:
        out.append(_footnotes_section_html(footnote_order, footnote_defs, render_inline))
    return "\n".join(out) + ("\n" if out else "")


# CommonMark HTML block starts: type 1 (until the closing tag) and type 6 (until a blank line).
_HTML_BLOCK_RAW = ("script", "style", "pre", "textarea")
_HTML_BLOCK_TAGS = frozenset(
    "address article aside blockquote body caption center col colgroup dd details dialog dir div dl dt "
    "fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr html legend li main menu nav ol "
    "optgroup option p section summary table tbody td tfoot th thead title tr ul".split()
)
_HTML_BLOCK_START_RE = re.compile(r"^ {0,3}<(?P<close>/?)(?P<tag>[A-Za-z][A-Za-z0-9-]*)(?=[\s/>]|$)")
_MARKDOWN_ATTR_RE = re.compile(r"""\s+markdown\s*=\s*(?:"1"|'1'|1)(?=[\s/>])""")
_ENTITY_AMP_RE = re.compile(r"&amp;(?=(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});)")
_CODE_SPAN_HTML_RE = re.compile(r"(<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>)", re.S)
_ATX_ATTR_HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t].*\}\s*$")
_FIRST_TAG_RE = re.compile(r"^(?P<lead>\s*<(?P<tag>[A-Za-z][A-Za-z0-9-]*))(?P<attrs>[^>]*)>")


def _html_block_kind(line: str) -> str | None:
    """``raw`` (type 1), ``block`` (type 6) or ``None`` for a line that starts no HTML block."""
    match = _HTML_BLOCK_START_RE.match(line)
    if not match:
        return None
    tag = match.group("tag").lower()
    if tag in _HTML_BLOCK_RAW and not match.group("close"):
        return "raw"
    return "block" if tag in _HTML_BLOCK_TAGS else None


def _keep_entities(html: str) -> str:
    """Undo the escape of ``&`` that starts an entity reference, outside code."""
    parts = _CODE_SPAN_HTML_RE.split(html)
    return "".join(part if index % 2 else _ENTITY_AMP_RE.sub("&", part) for index, part in enumerate(parts))


def _with_block_attrs(html: str, parsed: dict[str, Any]) -> str:
    """Add an IAL's id / classes / key="value" to the first tag of ``html``."""
    match = _FIRST_TAG_RE.match(html)
    if not match:
        return html
    attrs = match.group("attrs")
    extra = ""
    if parsed["id"] and not re.search(r"\sid\s*=", attrs):
        extra += f' id="{html_module.escape(parsed["id"])}"'
    if parsed["classes"]:
        joined = " ".join(parsed["classes"])
        found = re.search(r'\sclass="([^"]*)"', attrs)
        if found:
            attrs = attrs[: found.start(1)] + f"{found.group(1)} {html_module.escape(joined)}".strip() + attrs[found.end(1):]
        else:
            extra += f' class="{html_module.escape(joined)}"'
    for key, value in parsed["attrs"].items():
        extra += f' {html_module.escape(key)}="{html_module.escape(value)}"'
    return f"{match.group('lead')}{attrs}{extra}>" + html[match.end():]


def _markdown_to_html_blocks(content: str, *, raw_html: bool, attr_list: bool) -> str:
    """``markdown_to_html`` with HTML blocks and/or block IALs (see its docstring)."""
    lines = content.replace("\x00", "\ufffd").splitlines()
    out: list[str] = []
    buf: list[str] = []

    def render(md_lines: list[str]) -> str:
        html = markdown_to_html("\n".join(md_lines)).rstrip("\n")
        return _keep_entities(html) if raw_html else html

    def flush() -> None:
        if any(line.strip() for line in buf):
            out.append(render(buf))
        buf.clear()

    fence: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        kind = _html_block_kind(line) if raw_html and fence is None else None
        if kind is not None:
            flush()
            tag = _HTML_BLOCK_START_RE.match(line).group("tag").lower()
            opener = line.strip()
            if kind == "block" and _MARKDOWN_ATTR_RE.search(opener) and re.fullmatch(r"<[^<>]*>", opener):
                close = re.compile(rf"^ {{0,3}}</{re.escape(tag)}\s*>\s*$", re.I)
                opens = re.compile(rf"^ {{0,3}}<{re.escape(tag)}(?=[\s/>]|$)", re.I)
                depth, j = 1, i + 1
                while j < len(lines):
                    if opens.match(lines[j]):
                        depth += 1
                    elif close.match(lines[j]):
                        depth -= 1
                        if not depth:
                            break
                    j += 1
                if j < len(lines):
                    inner = _markdown_to_html_blocks("\n".join(lines[i + 1:j]), raw_html=raw_html, attr_list=attr_list)
                    out.append("\n".join(part for part in (_MARKDOWN_ATTR_RE.sub("", opener), inner.rstrip("\n"), lines[j].strip()) if part))
                    i = j + 1
                    continue
            j = i
            if kind == "raw":
                end = re.compile(rf"</{re.escape(tag)}\s*>", re.I)
                while j < len(lines) and not end.search(lines[j]):
                    j += 1
            else:
                while j + 1 < len(lines) and lines[j + 1].strip():
                    j += 1
            out.append("\n".join(lines[i:j + 1]))
            i = j + 1
            continue
        heading = _ATX_ATTR_HEADING_RE.match(line) if attr_list and fence is None else None
        if heading:
            trailing = _TRAILING_KRAMDOWN_IAL_RE.match(line) or _TRAILING_PANDOC_ATTR_RE.match(line)
            parsed = _parse_attr_list_inner(trailing.group("inner")) if trailing else None
            if parsed is not None and (parsed["id"] or parsed["classes"] or parsed["attrs"]):
                flush()
                out.append(_with_block_attrs(render([trailing.group("body").rstrip()]), parsed))
                i += 1
                continue
        standalone = _STANDALONE_IAL_RE.match(line) if attr_list and fence is None else None
        parsed = _parse_attr_list_inner(standalone.group("inner")) if standalone else None
        if parsed is not None and buf and buf[-1].strip():
            start = len(buf)
            while start and buf[start - 1].strip():
                start -= 1
            block = buf[start:]
            del buf[start:]
            flush()
            out.append(_with_block_attrs(render(block), parsed))
            i += 1
            continue
        fence, _, _ = _advance_fence(fence, line)
        buf.append(line)
        i += 1
    flush()
    return "\n".join(out) + ("\n" if out else "")


_ALERT_STYLESHEET = """\
.markdown-alert {
  padding: 0.75em 1em;
  margin: 1em 0;
  border-left: 0.25em solid #57606a;
  background: #f6f8fa;
}
.markdown-alert-title {
  font-weight: 700;
  margin: 0 0 0.35em;
}
.markdown-alert-note,
.markdown-alert-info,
.markdown-alert-message { border-left-color: #0969da; }
.markdown-alert-tip { border-left-color: #1a7f37; }
.markdown-alert-important { border-left-color: #8250df; }
.markdown-alert-warning,
.markdown-alert-warn { border-left-color: #9a6700; }
.markdown-alert-caution,
.markdown-alert-alert { border-left-color: #cf222e; }
.markdown-alert[data-alert-flavor="github"] { background: #f6f8fa; }
.markdown-alert[data-alert-flavor="obsidian"] { border-radius: 0.25em; }
.markdown-alert[data-alert-flavor="qiita"] { border-left-width: 0.35em; }
.markdown-alert[data-alert-flavor="zenn"] { background: #fff8f0; }
"""

_DEFAULT_STYLESHEET_REST = """\
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #d0d7de; padding: 0.4em 0.8em; }
th { background: #f6f8fa; font-weight: 600; }
blockquote {
  margin: 1em 0;
  padding: 0 1em;
  border-left: 0.25em solid #d0d7de;
  color: #656d76;
}
pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre {
  padding: 0.8em;
  overflow: auto;
  background: #f6f8fa;
  border-radius: 0.25em;
}
h1, h2, h3, h4, h5, h6 { margin-top: 1.25em; margin-bottom: 0.5em; }
del { text-decoration: line-through; }
li input[type="checkbox"] { margin: 0 0.35em 0 0; vertical-align: middle; }
details {
  margin: 1em 0;
  border: 1px solid #d0d7de;
  border-radius: 0.25em;
  padding: 0.5em 0.8em;
}
summary { cursor: pointer; font-weight: 600; }
.footnotes {
  margin-top: 2em;
  padding-top: 0.75em;
  border-top: 1px solid #d0d7de;
  font-size: 0.9em;
}
.footnotes ol { padding-left: 1.5em; }
sup.footnote-ref { font-size: 0.75em; line-height: 0; }
.footnote-backref { text-decoration: none; }
"""


def alert_stylesheet() -> str:
    """Return compact CSS for ``.markdown-alert`` HTML from ``markdown_to_html``.

    Covers the shared ``.markdown-alert`` / ``.markdown-alert-title`` hooks,
    kind colors (``note`` / ``tip`` / ``warning`` / Qiita ``warn`` / Zenn
    ``alert``, …), and ``data-alert-flavor`` distinctions. No external
    URLs or font files. Embed next to converted HTML::

        <style>{alert_stylesheet()}</style>
    """
    return _ALERT_STYLESHEET


def default_stylesheet() -> str:
    """Return compact CSS for ``markdown_to_html`` output, including alerts.

    Includes :func:`alert_stylesheet` plus defaults for tables, blockquotes,
    fenced/inline code, headings, strikethrough (``del``), task-list
    checkboxes, ``details``/``summary``, and ``.footnotes``. Stdlib string
    only; no external URLs.
    Typical embedding::

        html = "<style>" + default_stylesheet() + "</style>\\n" + markdown_to_html(src)
    """
    return _ALERT_STYLESHEET + _DEFAULT_STYLESHEET_REST


def is_probably_url(value: str) -> bool:
    """Return True if ``value`` looks like an http(s) URL."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


# === SECTION: directory tree ===
# Directory trees <-> Markdown nested lists. A tree is plain data:
# ``{name: subtree_dict}`` for a directory and ``{name: None}`` for a file,
# in display order. Canonical Markdown marks directories with a trailing
# ``/`` and nests children two spaces deeper.

_TREE_LIST_ITEM_RE = re.compile(r"^( *)[-*+][ \t]+(.+?)[ \t]*$")
_TREE_TEXT_BRANCH_RE = re.compile(r"(├──|└──|\|--|`--|\+--)[ \t]?")
_TREE_TEXT_LEVEL_WIDTH = 4


def _tree_entry_name(text: str) -> tuple[str, bool]:
    """Split a list/tree label into ``(name, is_directory)``."""
    name = text.strip()
    if len(name) >= 2 and name.startswith("`") and name.endswith("`"):
        name = name[1:-1].strip()
    is_dir = name.endswith("/")
    return name.rstrip("/"), is_dir


_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_NON_PORTABLE_NAME_CHARS = frozenset('<>:"|?*')


def _validate_tree_name(name: str, *, portable: bool = False) -> None:
    """Reject names a Markdown tree cannot represent (and, if ``portable``, non-Windows-safe ones).

    Representability applies everywhere: separators, ``.``/``..``, control
    characters, leading/trailing spaces and code-span-looking names would
    read back as a different tree. Portability applies only where files
    are *created* (:func:`scaffold_from_markdown`): a scaffold that silently
    creates ``a:b`` (an NTFS alternate data stream) or ``CON`` (a reserved
    device) breaks the repository for Windows users later, while a
    read-only listing of an existing POSIX directory must keep working.
    """
    if (
        name in {"", ".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
        or name[0] == " "
        or name[-1] == " "
        or (len(name) >= 2 and name[0] == "`" and name[-1] == "`")
    ):
        raise ValueError(f"invalid directory tree entry name: {name!r}")
    if portable and (
        any(ch in _NON_PORTABLE_NAME_CHARS for ch in name)
        or name[-1] == "."
        or name.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"directory tree entry name is not portable to Windows: {name!r}")


def _tree_insert(stack: list[tuple[int, dict[str, Any]]], depth: int, name: str, is_dir: bool) -> None:
    while stack and stack[-1][0] >= depth:
        stack.pop()
    if not stack:
        raise ValueError(f"directory tree entry {name!r} is nested under nothing")
    parent = stack[-1][1]
    _validate_tree_name(name)
    if name in parent:
        raise ValueError(f"duplicate directory tree entry: {name!r}")
    parent[name] = {} if is_dir else None
    if is_dir:
        stack.append((depth, parent[name]))


def markdown_to_directory_tree(content: str) -> dict[str, Any]:
    """Parse a nested Markdown bullet list into a directory tree mapping.

    Items ending in ``/`` (optionally wrapped in inline code) are
    directories; an item with nested children is a directory too. Fenced
    code and non-list lines are ignored. Names containing ``/``, ``\\``,
    or equal to ``.`` / ``..`` raise ``ValueError`` so a tree can never
    describe a path outside its root; so do control characters and
    leading/trailing spaces, which would not read back unchanged.
    Windows portability is checked only by :func:`scaffold_from_markdown`.
    """
    root: dict[str, Any] = {}
    entries: list[tuple[int, str, bool]] = []
    for item in _scan_lines(content.splitlines()):
        if item.in_fenced_code:
            continue
        match = _TREE_LIST_ITEM_RE.match(item.text.expandtabs(4))
        if not match:
            continue
        name, is_dir = _tree_entry_name(match.group(2))
        entries.append((len(match.group(1)), name, is_dir))
    # An entry followed by a deeper-indented entry is a directory.
    for index, (indent, name, is_dir) in enumerate(entries):
        if not is_dir and index + 1 < len(entries) and entries[index + 1][0] > indent:
            entries[index] = (indent, name, True)
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for indent, name, is_dir in entries:
        _tree_insert(stack, indent, name, is_dir)
    return root


def directory_tree_to_markdown(tree: dict[str, Any]) -> str:
    """Render a directory tree mapping as a canonical nested Markdown list.

    ``markdown_to_directory_tree(directory_tree_to_markdown(t)) == t`` for
    any tree built from valid names (see :func:`markdown_to_directory_tree`).
    """
    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        for name, child in node.items():
            _validate_tree_name(name)
            if child is None:
                lines.append(f"{'  ' * depth}- {name}")
            elif isinstance(child, dict):
                lines.append(f"{'  ' * depth}- {name}/")
                walk(child, depth + 1)
            else:
                raise ValueError(f"directory tree values must be dict or None, got {type(child).__name__}")

    if not isinstance(tree, dict):
        raise ValueError("directory tree must be a dict")
    walk(tree, 0)
    return "\n".join(lines) + ("\n" if lines else "")


def directory_to_markdown(
    path: str | Path,
    *,
    include_hidden: bool = False,
    max_depth: int | None = None,
) -> str:
    """Describe an existing directory as a canonical nested Markdown list.

    Read-only: entries are listed by name (sorted), symlinks are listed but
    never followed, and hidden (dot) entries are skipped unless
    ``include_hidden``. ``max_depth=1`` lists only the top level;
    ``max_depth=0`` and negative values are rejected, while ``None`` means
    unlimited depth. An entry
    whose name the tree format cannot represent portably (see
    :func:`markdown_to_directory_tree`) raises ``ValueError`` instead of
    producing Markdown that would read back as a different tree.
    """
    if max_depth is not None and max_depth < 1:
        raise ValueError("max_depth must be >= 1 or None")
    base = Path(path)
    if not base.is_dir():
        raise ValueError(f"not a directory: {base}")

    def walk(directory: Path, depth: int) -> dict[str, Any]:
        node: dict[str, Any] = {}
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if not include_hidden and entry.name.startswith("."):
                continue
            if entry.is_dir() and not entry.is_symlink():
                if max_depth is not None and depth + 1 >= max_depth:
                    node[entry.name] = {}
                else:
                    node[entry.name] = walk(entry, depth + 1)
            else:
                node[entry.name] = None
        return node

    return directory_tree_to_markdown(walk(base, 0))


def tree_text_to_markdown(text: str) -> str:
    """Convert ``tree``-command style text into a canonical Markdown list.

    Accepts Unicode (``├──`` / ``└──`` / ``│``) and ASCII (``|--`` /
    ``\\`--``) connectors with 4-column levels. A leading root line without
    a connector (e.g. ``.`` or ``project/``) is skipped. Summary lines such
    as ``3 directories, 5 files`` and blank lines are ignored. Symlink
    lines (``name -> target``) keep only the link name. Literal `` -> `` in
    an entry name is ambiguous and is interpreted as a symlink delimiter.
    """
    entries: list[tuple[int, str, bool]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        match = _TREE_TEXT_BRANCH_RE.search(line)
        if not match:
            continue
        depth, remainder = divmod(match.start(), _TREE_TEXT_LEVEL_WIDTH)
        if remainder:
            raise ValueError(f"tree line is not aligned to {_TREE_TEXT_LEVEL_WIDTH}-column levels: {raw!r}")
        entry = line[match.end():]
        if " -> " in entry:
            # `tree` prints symlinks as "name -> target"; keep only the link name.
            entry = entry.split(" -> ", 1)[0]
        name, is_dir = _tree_entry_name(entry)
        entries.append((depth, name, is_dir))
    for index, (depth, name, is_dir) in enumerate(entries):
        if not is_dir and index + 1 < len(entries) and entries[index + 1][0] > depth:
            entries[index] = (depth, name, True)
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for depth, name, is_dir in entries:
        _tree_insert(stack, depth, name, is_dir)
    return directory_tree_to_markdown(root)


def markdown_to_tree_text(content: str, *, root: str = ".") -> str:
    """Render a Markdown directory list as ``tree``-command style text.

    ``tree_text_to_markdown(markdown_to_tree_text(md)) == md`` for canonical
    Markdown produced by :func:`directory_tree_to_markdown`.
    """
    tree = markdown_to_directory_tree(content)
    lines = [root]

    def walk(node: dict[str, Any], prefix: str) -> None:
        items = list(node.items())
        for index, (name, child) in enumerate(items):
            last = index == len(items) - 1
            label = f"{name}/" if isinstance(child, dict) else name
            lines.append(f"{prefix}{'└── ' if last else '├── '}{label}")
            if isinstance(child, dict):
                walk(child, prefix + ("    " if last else "│   "))

    walk(tree, "")
    return "\n".join(lines) + "\n"


def scaffold_from_markdown(content: str, root: str | Path) -> list[str]:
    """Create the directories and empty files described by a Markdown list.

    Existing files are never overwritten or truncated and existing
    directories are reused. Every path is validated to stay inside
    ``root`` (``..``, separators, absolute names, and names that are not
    portable to Windows such as ``a:b`` or ``CON`` are rejected before
    anything is created). Returns the POSIX-style relative paths that were
    newly created, in creation order.
    """
    tree = markdown_to_directory_tree(content)
    base = Path(root)
    resolved_base = base.resolve()
    created: list[str] = []

    def plan(node: dict[str, Any], parts: tuple[str, ...]) -> None:
        for name, child in node.items():
            _validate_tree_name(name, portable=True)
            target = resolved_base.joinpath(*parts, name).resolve()
            rel = "/".join(parts + (name,))
            if resolved_base not in target.parents:
                raise ValueError(f"scaffold path escapes root: {rel}")
            if isinstance(child, dict):
                if target.exists() and not target.is_dir():
                    raise FileExistsError(f"scaffold directory collides with a file: {rel}")
                plan(child, parts + (name,))
            elif target.is_dir():
                raise FileExistsError(f"scaffold file collides with a directory: {rel}")

    def build(node: dict[str, Any], parts: tuple[str, ...]) -> None:
        for name, child in node.items():
            target = base.joinpath(*parts, name)
            rel = "/".join(parts + (name,))
            if isinstance(child, dict):
                if target.exists() and not target.is_dir():
                    raise FileExistsError(f"scaffold directory collides with a file: {rel}")
                if not target.exists():
                    target.mkdir()
                    created.append(rel + "/")
                build(child, parts + (name,))
            else:
                if target.is_dir():
                    raise FileExistsError(f"scaffold file collides with a directory: {rel}")
                if not target.exists():
                    target.touch()
                    created.append(rel)

    plan(tree, ())
    base.mkdir(parents=True, exist_ok=True)
    build(tree, ())
    return created


# === SECTION: markdown lite model ===
# A deliberately small block/inline model of the Markdown subset that
# one-way and dialect writers share: ATX headings, paragraphs (one physical
# line at a time, never reflowed), fenced code, bullet/ordered list items,
# ``>`` quote lines, thematic breaks, and inline code / links / images /
# autolinks / bold / italic / strikethrough. Anything else (tables, raw
# HTML, footnotes, ...) passes through as literal paragraph text.

_LITE_LIST_RE = re.compile(r"^( *)([-*+]|\d+[.)])[ \t]+(.*)$")
_LITE_QUOTE_RE = re.compile(r"^ {0,3}> ?(.*)$")
_LITE_INLINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("image", re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")),
    ("link", re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")),
    ("autolink", re.compile(r"<((?:https?|mailto):[^>\s]+)>")),
    ("bold", re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")),
    ("strike", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")),
    ("italic", re.compile(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*|(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])")),
)


def _lite_blocks(content: str) -> list[tuple[Any, ...]]:
    """Split Markdown into ``(kind, ...)`` blocks for the lite writers."""
    blocks: list[tuple[Any, ...]] = []
    lines = content.splitlines()
    scanned = _scan_lines(lines)
    list_indents: list[int] = []
    index = 0
    while index < len(lines):
        item = scanned[index]
        line = item.text
        if item.is_fence_open:
            match = _FENCE_RE.match(line)
            info = match.group(2).strip() if match else ""
            lang = info.split()[0] if info else ""
            body: list[str] = []
            index += 1
            while index < len(lines) and not scanned[index].is_fence_close:
                body.append(lines[index])
                index += 1
            index += 1
            blocks.append(("code", lang, "\n".join(body)))
            list_indents = []
            continue
        heading_match = _HEADING_RE.match(line)
        list_match = _LITE_LIST_RE.match(line.expandtabs(4))
        quote_match = _LITE_QUOTE_RE.match(line)
        if not line.strip():
            blocks.append(("blank",))
        elif heading_match:
            blocks.append(("heading", len(heading_match.group(1)), heading_match.group(2).strip().rstrip("#").strip()))
            list_indents = []
        elif _HR_RE.match(line) and not list_match:
            blocks.append(("hr",))
        elif list_match:
            indent = len(list_match.group(1))
            while list_indents and list_indents[-1] > indent:
                list_indents.pop()
            if not list_indents or list_indents[-1] < indent:
                list_indents.append(indent)
            ordered = list_match.group(2)[0].isdigit()
            blocks.append(("list", ordered, len(list_indents) - 1, list_match.group(3)))
        elif quote_match:
            blocks.append(("quote", quote_match.group(1)))
        else:
            blocks.append(("para", line))
            list_indents = []
        index += 1
    return blocks


def _lite_inline(text: str) -> list[tuple[Any, ...]]:
    """Tokenize one line of inline Markdown into nested ``(kind, ...)`` tokens."""
    tokens: list[tuple[Any, ...]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            tokens.append(("text", "".join(buffer)))
            buffer.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and not text[index + 1].isalnum() and not text[index + 1].isspace():
            buffer.append(text[index + 1])
            index += 2
            continue
        if char == "`":
            end = index
            while end < len(text) and text[end] == "`":
                end += 1
            marker = text[index:end]
            close = text.find(marker, end)
            if close >= 0:
                flush()
                inner = text[end:close]
                if len(inner) >= 2 and inner.startswith(" ") and inner.endswith(" ") and inner.strip():
                    inner = inner[1:-1]
                tokens.append(("code", inner))
                index = close + len(marker)
                continue
            buffer.append(marker)
            index = end
            continue
        matched = False
        for kind, pattern in _LITE_INLINE_PATTERNS:
            match = pattern.match(text, index)
            if not match:
                continue
            flush()
            if kind == "image":
                if _sanitize_url_scheme(match.group(2)):
                    tokens.append(("image", match.group(1), match.group(2)))
                elif match.group(1):
                    tokens.append(("text", match.group(1)))
            elif kind == "link":
                label = _lite_inline(match.group(1))
                if _sanitize_url_scheme(match.group(2)):
                    tokens.append(("link", label, match.group(2)))
                else:
                    # Same URL policy as the HTML path: a javascript:/data:/
                    # shell: target is dropped and only its label survives.
                    tokens.extend(label)
            elif kind == "autolink":
                tokens.append(("link", [("text", match.group(1))], match.group(1)))
            else:
                inner = next(group for group in match.groups() if group is not None)
                tokens.append((kind, _lite_inline(inner)))
            index = match.end()
            matched = True
            break
        if matched:
            continue
        buffer.append(char)
        index += 1
    flush()
    return tokens


def _lite_plain(tokens: list[tuple[Any, ...]]) -> str:
    """Flatten inline tokens to their visible text."""
    parts: list[str] = []
    for token in tokens:
        kind = token[0]
        if kind in {"text", "code"}:
            parts.append(token[1])
        elif kind == "image":
            parts.append(token[1])
        else:
            parts.append(_lite_plain(token[1]))
    return "".join(parts)


# === SECTION: system formats ===
# One-way/system-facing adapters from #24 P9: calendar and platform
# snapshots as tables, RFC 5322 email <-> Markdown, and Markdown -> man(7).

_ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_ENGLISH_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def calendar_to_markdown(year: int, month: int, *, firstweekday: int = 0, title: str | None = None) -> str:
    """Render one month as a heading plus a GFM table of day numbers.

    Month/weekday names are fixed English strings (not the process locale)
    so output is deterministic. ``firstweekday`` follows
    :mod:`calendar` (0 = Monday, 6 = Sunday).
    """
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1..12, got {month}")
    if not 0 <= firstweekday <= 6:
        raise ValueError(f"firstweekday must be 0..6, got {firstweekday}")
    cal = calendar_module.Calendar(firstweekday)
    headers = [_ENGLISH_WEEKDAYS[(firstweekday + offset) % 7] for offset in range(7)]
    rows = [[str(day) if day else "" for day in week] for week in cal.monthdayscalendar(year, month)]
    label = title if title is not None else f"{_ENGLISH_MONTHS[month - 1]} {year}"
    return heading(label, 2) + "\n" + table(headers, rows)


def platform_to_markdown(*, title: str = "Environment") -> str:
    """Summarize the running interpreter/OS as a two-column Markdown table.

    Reads only :mod:`platform` / :mod:`sys` facts useful for bug reports.
    The host name, user name, and environment variables are deliberately
    never included.
    """
    rows = [
        ("Python", platform.python_version()),
        ("Implementation", platform.python_implementation()),
        ("Compiler", platform.python_compiler()),
        ("System", platform.system() or "unknown"),
        ("Release", platform.release() or "unknown"),
        ("Machine", platform.machine() or "unknown"),
        ("Platform", platform.platform(terse=True)),
        ("Byte order", sys.byteorder),
    ]
    return heading(title, 2) + "\n" + table(["Key", "Value"], rows)


_EMAIL_SUMMARY_HEADERS = ("From", "To", "Cc", "Date", "Subject")


def email_to_markdown(message: str | bytes) -> str:
    """Render an RFC 5322 / MIME message as Markdown without side effects.

    Output: ``# Subject``, a header table (From/To/Cc/Date/Subject that are
    present), the first ``text/plain`` body verbatim (or the first
    ``text/html`` body through :func:`html_to_markdown` when there is no
    plain part), then an attachment table (filename, content type, decoded
    size). Attachments are never decoded to disk, opened, or executed.
    """
    policy = email_policy.default
    parsed = (
        email_module.message_from_bytes(message, policy=policy)
        if isinstance(message, bytes)
        else email_module.message_from_string(message, policy=policy)
    )
    subject = str(parsed.get("Subject", "") or "").strip() or "(no subject)"
    rows = [(name, str(parsed[name])) for name in _EMAIL_SUMMARY_HEADERS if parsed.get(name) is not None]
    out = [heading(subject, 1), "\n", table(["Header", "Value"], rows)]

    body_part = parsed.get_body(preferencelist=("plain", "html"))
    if body_part is not None:
        try:
            body = body_part.get_content()
        except (LookupError, UnicodeDecodeError):
            body = (body_part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if body_part.get_content_type() == "text/html":
            body = html_to_markdown(body)
        body = body.replace("\r\n", "\n").strip("\n")
        if body:
            out += ["\n", body, "\n"]

    attachments = []
    for part in parsed.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append((part.get_filename() or "", part.get_content_type(), str(len(payload))))
    if attachments:
        out += ["\n", heading("Attachments", 2), "\n", table(["Filename", "Content-Type", "Size (bytes)"], attachments)]
    return "".join(out)


def markdown_to_email(
    content: str,
    *,
    subject: str,
    sender: str,
    to: str | list[str],
    date: Any = None,
) -> str:
    """Build (never send) a ``multipart/alternative`` email from Markdown.

    The ``text/plain`` part is the Markdown source verbatim; the
    ``text/html`` part is :func:`markdown_to_html` output. Header values
    containing CR/LF are rejected by :mod:`email.policy` (no header
    injection). ``date`` may be a :class:`datetime.datetime`; omitted means
    no ``Date`` header, keeping output deterministic.
    ``email_to_markdown()`` trims leading and trailing blank lines from the
    recovered body, so those surrounding blank lines are not byte-verbatim.
    """
    msg = email_message.EmailMessage(policy=email_policy.default)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
    if date is not None:
        msg["Date"] = email_utils.format_datetime(date)
    body = content if content.endswith("\n") else content + "\n"
    msg.set_content(body, subtype="plain", charset="utf-8")
    msg.add_alternative(markdown_to_html(content), subtype="html", charset="utf-8")
    return msg.as_string()


def _man_escape(text: str) -> str:
    return text.replace("\x00", "\uFFFD").replace("\\", "\\e").replace("-", "\\-")


def _man_inline(tokens: list[tuple[Any, ...]], font: str = "R") -> str:
    parts: list[str] = []
    for token in tokens:
        kind = token[0]
        if kind == "text":
            parts.append(_man_escape(token[1]))
        elif kind == "code":
            parts.append(f"\\fB{_man_escape(token[1])}\\f{font}")
        elif kind == "bold":
            parts.append(f"\\fB{_man_inline(token[1], 'B')}\\f{font}")
        elif kind == "italic":
            parts.append(f"\\fI{_man_inline(token[1], 'I')}\\f{font}")
        elif kind == "strike":
            parts.append(_man_inline(token[1], font))
        elif kind == "link":
            label = _man_inline(token[1], font)
            url = _man_escape(token[2])
            parts.append(label if _lite_plain(token[1]) == token[2] else f"{label} <{url}>")
        elif kind == "image":
            parts.append(f"[{_man_escape(token[1])}] <{_man_escape(token[2])}>")
    return "".join(parts)


def _man_line(text: str) -> str:
    """Guard a rendered text line so troff never reads it as a request."""
    return "\\&" + text if text.startswith((".", "'")) else text


def markdown_to_man(
    content: str,
    *,
    name: str,
    section: str = "1",
    date: str = "",
    source: str = "",
    manual: str = "",
) -> str:
    """Render the Markdown lite subset as a man(7) troff page (one-way).

    ``# H`` -> ``.SH`` (upper-cased), deeper headings -> ``.SS``, lines ->
    ``.PP`` paragraphs, fenced code -> ``.EX``/``.EE``, lists -> ``.IP``,
    quotes -> ``.RS``/``.RE``, bold/code -> ``\\fB``, italic -> ``\\fI``.
    NUL in the Markdown body is replaced with U+FFFD. Backslashes and
    hyphens are escaped and any line that would start with
    ``.`` or ``'`` is guarded with ``\\&``, so Markdown text can never
    inject a troff request.
    ``name``/``section``/``date``/``source``/``manual`` go on the ``.TH``
    line, so control characters (including CR/LF) in them raise
    ``ValueError`` rather than starting a new troff request line.
    """
    for label, value in (("name", name), ("section", section), ("date", date), ("source", source), ("manual", manual)):
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(f"markdown_to_man {label} must not contain control characters: {value!r}")

    def quoted(value: str) -> str:
        return '"' + _man_escape(value).replace('"', '\\(dq') + '"'

    out = [f".TH {quoted(name.upper())} {quoted(section)} {quoted(date)} {quoted(source)} {quoted(manual)}"]
    in_paragraph = False
    in_quote = False
    numberer = _MarkdownListNumberer()
    for block in _lite_blocks(content):
        kind = block[0]
        if kind not in {"list", "blank"}:
            numberer.reset()
        if in_quote and kind != "quote":
            out.append(".RE")
            in_quote = False
        if kind == "blank":
            in_paragraph = False
        elif kind == "heading":
            tokens = _lite_inline(block[2])
            if block[1] == 1:
                out.append(".SH " + _man_escape(_lite_plain(tokens).upper()))
            else:
                out.append(".SS " + _man_inline(tokens))
            in_paragraph = False
        elif kind == "code":
            out.append(".PP")
            out.append(".EX")
            out.extend(_man_line(_man_escape(line)) for line in block[2].split("\n"))
            out.append(".EE")
            in_paragraph = False
        elif kind == "list":
            ordered, depth, text = block[1], block[2], block[3]
            count = numberer.count(depth, ordered)
            bullet = f"{count}." if ordered else "\\(bu"
            out.append(f".IP {bullet} {4 + depth * 2}")
            out.append(_man_line(_man_inline(_lite_inline(text))))
            in_paragraph = True
        elif kind == "quote":
            if not in_quote:
                out.append(".RS 4")
                in_quote = True
            out.append(_man_line(_man_inline(_lite_inline(block[1]))))
        elif kind == "hr":
            out.append(".PP")
            in_paragraph = False
        else:
            if not in_paragraph:
                out.append(".PP")
                in_paragraph = True
            out.append(_man_line(_man_inline(_lite_inline(block[1]))))
    if in_quote:
        out.append(".RE")
    return "\n".join(out) + "\n"


# === SECTION: dialects ===
# #24 P7 dialect conversions. Each text dialect declares a canonical
# Markdown subset (the markdown lite model: ATX headings, one-line
# paragraphs, fenced code, "-" bullets / "1." ordered items nested two
# spaces per level, ">" quote lines, "---", and inline **bold** / *italic*
# / ~~strike~~ / `code` / [label](url)). On that subset
# ``dialect_to_markdown(markdown_to_dialect(md)) == md`` except where a
# dialect has no equivalent construct (documented per function). Literal
# markup characters inside plain text are not escaped for the target.

# One private-use escape code point. Any literal occurrence in the input is
# doubled to ESC+"E" first, so placeholders (ESC+"P<n>;") and emphasis
# sentinels (ESC+"B"/"I"/"S") can never be forged by input text.
_DIALECT_ESC = "\ue000"
_DIALECT_MARK = {"bold": _DIALECT_ESC + "B", "italic": _DIALECT_ESC + "I", "strike": _DIALECT_ESC + "S"}
_DIALECT_MD_MARK = {_DIALECT_ESC + "B": "**", _DIALECT_ESC + "I": "*", _DIALECT_ESC + "S": "~~"}
_DIALECT_PLACEHOLDER_RE = re.compile(_DIALECT_ESC + r"P(\d+);")


def _dialect_inline_to_markdown(
    text: str,
    *,
    code: re.Pattern[str],
    links: tuple[tuple[re.Pattern[str], Any], ...],
    emphasis: tuple[tuple[re.Pattern[str], str], ...],
    unescape: Any = None,
) -> str:
    """Convert one line of dialect inline markup to canonical Markdown.

    Code spans and links are swapped for placeholders first so emphasis
    rules never fire inside them; emphasis markers are written as private
    sentinels and only turned into Markdown markers at the end, so one
    rule's output can never be re-read by the next rule.
    """
    stash: list[str] = []

    def keep(rendered: str) -> str:
        stash.append(rendered)
        return f"{_DIALECT_ESC}P{len(stash) - 1};"

    text = text.replace(_DIALECT_ESC, _DIALECT_ESC + "E")
    text = code.sub(
        lambda m: keep(inline_code(unescape(m.group(1)) if unescape is not None else m.group(1))),
        text,
    )
    for pattern, render in links:
        text = pattern.sub(lambda m, render=render: keep(render(m)), text)
    for pattern, kind in emphasis:
        mark = _DIALECT_MARK[kind]
        text = pattern.sub(lambda m, mark=mark: mark + m.group(1) + mark, text)
    for sentinel, marker in _DIALECT_MD_MARK.items():
        text = text.replace(sentinel, marker)
    if unescape is not None:
        text = unescape(text)
    # Stashed renderings can nest (a link label holding a code placeholder).
    # Input cannot forge a placeholder, so restoring until stable terminates.
    while True:
        restored = _DIALECT_PLACEHOLDER_RE.sub(lambda m: stash[int(m.group(1))], text)
        if restored == text:
            break
        text = restored
    return text.replace(_DIALECT_ESC + "E", _DIALECT_ESC)


class _MarkdownListNumberer:
    """Emit canonical Markdown list prefixes (2-space nesting, 1..n numbering)."""

    def __init__(self) -> None:
        self.counters: dict[int, tuple[bool, int]] = {}

    def reset(self) -> None:
        self.counters = {}

    def count(self, depth: int, ordered: bool) -> int:
        for level in [level for level in self.counters if level > depth]:
            del self.counters[level]
        was_ordered, count = self.counters.get(depth, (ordered, 0))
        count = count + 1 if was_ordered == ordered else 1
        self.counters[depth] = (ordered, count)
        return count

    def prefix(self, depth: int, ordered: bool) -> str:
        count = self.count(depth, ordered)
        return "  " * depth + (f"{count}. " if ordered else "- ")


def _depth_from_indent(stack: list[int], indent: int) -> int:
    while stack and stack[-1] > indent:
        stack.pop()
    if not stack or stack[-1] < indent:
        stack.append(indent)
    return len(stack) - 1


def _dialect_code_block_lines(body: list[str], lang: str) -> list[str]:
    return code_block("\n".join(body), lang).rstrip("\n").split("\n")


def _render_with(inline: Any, tokens: list[tuple[Any, ...]]) -> str:
    return "".join(inline(token) for token in tokens)


# --- Slack mrkdwn -----------------------------------------------------------

def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_token(token: tuple[Any, ...]) -> str:
    kind = token[0]
    if kind == "text":
        return _slack_escape(token[1])
    if kind == "code":
        return f"`{_slack_escape(token[1])}`"
    if kind in {"bold", "italic", "strike"}:
        mark = {"bold": "*", "italic": "_", "strike": "~"}[kind]
        return mark + _render_with(_slack_token, token[1]) + mark
    if kind == "link":
        label = _lite_plain(token[1]).replace("|", "¦")
        return f"<{token[2]}>" if label == token[2] else f"<{token[2]}|{_slack_escape(label)}>"
    if kind == "image":
        return f"<{token[2]}|{_slack_escape(token[1] or token[2])}>"
    return ""


def markdown_to_slack_mrkdwn(content: str) -> str:
    """Convert the Markdown lite subset to Slack ``mrkdwn`` text.

    ``**b**`` -> ``*b*``, ``*i*`` -> ``_i_``, ``~~s~~`` -> ``~s~``, links ->
    ``<url|label>``, bullets -> ``•`` (4 spaces per nesting level), and
    ``&``/``<``/``>`` are entity-escaped as Slack requires. Slack has no
    headings, ordered lists, or code-block languages: headings become a
    bold line, ordered items keep their literal ``N.`` text, and the fence
    language is dropped (all lossy by design).
    """
    out: list[str] = []
    numberer = _MarkdownListNumberer()
    for block in _lite_blocks(content):
        kind = block[0]
        if kind not in {"list", "blank"}:
            numberer.reset()
        if kind == "blank":
            out.append("")
        elif kind == "heading":
            out.append("*" + _slack_escape(_lite_plain(_lite_inline(block[2]))) + "*")
        elif kind == "code":
            out.extend(["```", *[_slack_escape(line) for line in block[2].split("\n")], "```"])
        elif kind == "list":
            count = numberer.count(block[2], block[1])
            bullet = f"{count}. " if block[1] else "\u2022 "
            out.append("    " * block[2] + bullet + _render_with(_slack_token, _lite_inline(block[3])))
        elif kind == "quote":
            out.append("> " + _render_with(_slack_token, _lite_inline(block[1])))
        elif kind == "hr":
            out.append("---")
        else:
            out.append(_render_with(_slack_token, _lite_inline(block[1])))
    return "\n".join(out) + ("\n" if out else "")


_SLACK_CODE_RE = re.compile(r"`([^`\n]+)`")
_SLACK_LINK_RE = re.compile(r"<((?:https?|mailto):[^|>\s]+)(?:\|([^>]*))?>")
_SLACK_EMPHASIS = (
    (re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), "bold"),
    (re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])"), "italic"),
    (re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])"), "strike"),
)
_SLACK_BULLET_RE = re.compile("^( *)[\u2022\u25e6\u25aa-] +(.*)$")
_SLACK_ORDERED_RE = re.compile(r"^( *)\d+[.)] +(.*)$")


def _slack_unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _slack_link_to_markdown(match: re.Match[str]) -> str:
    url, label = match.group(1), match.group(2)
    if label is None or _slack_unescape(label) == url:
        return f"<{url}>"
    return f"[{_slack_unescape(label)}]({url})"


def _slack_inline(text: str) -> str:
    return _dialect_inline_to_markdown(
        text,
        code=_SLACK_CODE_RE,
        links=((_SLACK_LINK_RE, _slack_link_to_markdown),),
        emphasis=_SLACK_EMPHASIS,
        unescape=_slack_unescape,
    )


def slack_mrkdwn_to_markdown(content: str) -> str:
    """Convert Slack ``mrkdwn`` text to canonical Markdown.

    Reverses :func:`markdown_to_slack_mrkdwn` for paragraphs, bullets,
    ``1.`` items, ``>`` quotes, triple-backtick blocks, and inline
    bold/italic/strike/code/links. User/channel mentions such as
    ``<@U123>`` / ``<#C123>`` / ``<!here>`` are left as literal text.
    """
    out: list[str] = []
    numberer = _MarkdownListNumberer()
    indents: list[int] = []
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() == "```":
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "```":
                body.append(_slack_unescape(lines[index]))
                index += 1
            index += 1
            out.extend(_dialect_code_block_lines(body, ""))
            numberer.reset()
            indents = []
            continue
        bullet = _SLACK_BULLET_RE.match(line)
        ordered = _SLACK_ORDERED_RE.match(line)
        if bullet or ordered:
            match = bullet or ordered
            depth = _depth_from_indent(indents, len(match.group(1)))
            out.append(numberer.prefix(depth, ordered is not None) + _slack_inline(match.group(2)))
        elif line.startswith(">"):
            out.append("> " + _slack_inline(line[1:].lstrip()))
            numberer.reset()
            indents = []
        elif not line.strip():
            out.append("")
        elif line.strip() == "---":
            out.append("---")
        else:
            out.append(_slack_inline(line))
            numberer.reset()
            indents = []
        index += 1
    return "\n".join(out) + ("\n" if out else "")


# --- LLM chat messages ------------------------------------------------------

CHAT_ROLES = ("system", "developer", "user", "assistant", "tool")
_CHAT_HEADING_RE = re.compile(r"^## (" + "|".join(CHAT_ROLES) + r")[ \t]*\r?$", re.IGNORECASE)


def chat_messages_to_markdown(messages: Any) -> str:
    """Render ``[{"role": ..., "content": ...}]`` as ``## Role`` sections.

    Only string ``content`` and the roles in :data:`CHAT_ROLES` are
    supported; extra keys (``name``, ``tool_calls``, ...) or multimodal
    content raise ``ValueError`` rather than being silently dropped. A
    message whose content contains a line that would itself parse as a
    role heading outside fenced code is rejected as ambiguous, so
    ``markdown_to_chat_messages(chat_messages_to_markdown(m)) == m`` for
    every accepted list whose contents carry no leading/trailing newlines.
    """
    parts: list[str] = []
    for position, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError(f"message {position} must be a dict with exactly 'role' and 'content'")
        role, text = message["role"], message["content"]
        if role not in CHAT_ROLES:
            raise ValueError(f"message {position} has unsupported role {role!r}")
        if not isinstance(text, str):
            raise ValueError(f"message {position} content must be a string")
        scanned = _scan_lines(text.splitlines())
        for item in scanned:
            if not item.in_fenced_code and _CHAT_HEADING_RE.match(item.text):
                raise ValueError(f"message {position} content contains a role heading line: {item.text!r}")
        if sum(item.is_fence_open for item in scanned) != sum(item.is_fence_close for item in scanned):
            raise ValueError(f"message {position} content contains an unclosed fenced code block")
        section_text = f"## {role.capitalize()}\n"
        if text:
            section_text += f"\n{text}\n"
        parts.append(section_text)
    return "\n".join(parts)


def markdown_to_chat_messages(content: str) -> list[dict[str, str]]:
    """Parse ``## System`` / ``## User`` / ``## Assistant`` ... sections.

    Role headings are exact ``## <role>`` lines (case-insensitive) outside
    fenced code; any other heading (``## Summary``) is ordinary content.
    Text before the first role heading raises ``ValueError``. Lines are
    split only on LF, so CR and other Unicode line separators inside message
    content remain unchanged. Each
    message's content has surrounding LF characters stripped after parsing.
    """
    sections: list[tuple[str, list[str]]] = []
    for item in _scan_lines(content.split("\n")):
        match = None if item.in_fenced_code else _CHAT_HEADING_RE.match(item.text)
        if match:
            sections.append((match.group(1).lower(), []))
        elif sections:
            sections[-1][1].append(item.text)
        elif item.text.strip():
            raise ValueError(f"text before the first role heading: {item.text!r}")
    return [{"role": role, "content": "\n".join(lines).strip("\n")} for role, lines in sections]


# --- Org-mode ---------------------------------------------------------------

def _org_token(token: tuple[Any, ...]) -> str:
    kind = token[0]
    if kind == "text":
        return token[1]
    if kind == "code":
        return f"~{token[1]}~"
    if kind in {"bold", "italic", "strike"}:
        mark = {"bold": "*", "italic": "/", "strike": "+"}[kind]
        return mark + _render_with(_org_token, token[1]) + mark
    if kind == "link":
        label = _render_with(_org_token, token[1])
        return f"[[{token[2]}]]" if _lite_plain(token[1]) == token[2] else f"[[{token[2]}][{label}]]"
    if kind == "image":
        return f"[[{token[2]}]]"
    return ""


def markdown_to_org(content: str) -> str:
    """Convert the Markdown lite subset to Org-mode.

    Headings -> ``*`` stars, ``**b**`` -> ``*b*``, ``*i*`` -> ``/i/``,
    ``~~s~~`` -> ``+s+``, code -> ``~c~``, links -> ``[[url][label]]``,
    fences -> ``#+BEGIN_SRC lang`` (lines starting with ``*`` / ``#+``
    are comma-escaped per Org), ``>`` runs -> ``#+BEGIN_QUOTE``.
    """
    out: list[str] = []
    in_quote = False
    numberer = _MarkdownListNumberer()
    for block in _lite_blocks(content):
        kind = block[0]
        if kind not in {"list", "blank"}:
            numberer.reset()
        if in_quote and kind != "quote":
            out.append("#+END_QUOTE")
            in_quote = False
        if kind == "blank":
            out.append("")
        elif kind == "heading":
            out.append("*" * block[1] + " " + _render_with(_org_token, _lite_inline(block[2])))
        elif kind == "code":
            out.append(("#+BEGIN_SRC " + block[1]).rstrip())
            for line in block[2].split("\n") if block[2] else []:
                out.append("," + line if line.startswith(("*", "#+", ",*", ",#+")) else line)
            out.append("#+END_SRC")
        elif kind == "list":
            out.append(numberer.prefix(block[2], block[1]) + _render_with(_org_token, _lite_inline(block[3])))
        elif kind == "quote":
            if not in_quote:
                out.append("#+BEGIN_QUOTE")
                in_quote = True
            out.append(_render_with(_org_token, _lite_inline(block[1])))
        elif kind == "hr":
            out.append("-----")
        else:
            out.append(_render_with(_org_token, _lite_inline(block[1])))
    if in_quote:
        out.append("#+END_QUOTE")
    return "\n".join(out) + ("\n" if out else "")


_ORG_CODE_RE = re.compile(r"(?<![\w~=])[~=](?=\S)([^~=\n]+?)(?<=\S)[~=](?![\w~=])")
_ORG_LINK_RE = re.compile(r"\[\[([^\]\n]+)\](?:\[([^\]\n]+)\])?\]")
_ORG_EMPHASIS = (
    (re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])"), "bold"),
    (re.compile(r"(?<![\w/:])/(?=[^\s/])([^/\n]+?)(?<=[^\s/])/(?![\w/])"), "italic"),
    (re.compile(r"(?<![\w+])\+(?=[^\s+])([^+\n]+?)(?<=[^\s+])\+(?![\w+])"), "strike"),
)
_ORG_HEADING_RE = re.compile(r"^(\*+)[ \t]+(.*?)[ \t]*$")
_ORG_LIST_RE = re.compile(r"^( *)([-+]|\d+[.)])[ \t]+(.*)$")
_ORG_BLOCK_RE = re.compile(r"^[ \t]*#\+(BEGIN|END)_(\w+)(?:[ \t]+(\S+))?", re.IGNORECASE)


def _org_link_to_markdown(match: re.Match[str]) -> str:
    target, label = match.group(1), match.group(2)
    if not _sanitize_url_scheme(target):
        return _org_inline(label) if label is not None else target
    if label is None:
        return f"<{target}>" if re.match(r"(?:https?|mailto):", target) else f"[{target}]({target})"
    return f"[{_org_inline(label)}]({target})"


def _org_inline(text: str) -> str:
    return _dialect_inline_to_markdown(
        text,
        code=_ORG_CODE_RE,
        links=((_ORG_LINK_RE, _org_link_to_markdown),),
        emphasis=_ORG_EMPHASIS,
    )


def org_to_markdown(content: str) -> str:
    """Convert Org-mode to canonical Markdown (the :func:`markdown_to_org` subset).

    Supports ``*`` headings, ``-``/``+``/``1.`` lists, ``#+BEGIN_SRC`` /
    ``#+BEGIN_EXAMPLE`` / ``#+BEGIN_QUOTE`` blocks, ``-----`` rules, and
    ``*b*`` / ``/i/`` / ``+s+`` / ``~c~`` / ``=v=`` / ``[[url][label]]``.
    Other ``#+KEYWORD:`` lines, drawers, tables, and underline ``_u_`` are
    kept as literal text.
    """
    out: list[str] = []
    numberer = _MarkdownListNumberer()
    indents: list[int] = []
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        block = _ORG_BLOCK_RE.match(line)
        if block and block.group(1).upper() == "BEGIN":
            name = block.group(2).upper()
            body: list[str] = []
            index += 1
            while index < len(lines):
                end = _ORG_BLOCK_RE.match(lines[index])
                if end and end.group(1).upper() == "END" and end.group(2).upper() == name:
                    break
                body.append(lines[index])
                index += 1
            index += 1
            if name == "QUOTE":
                out.extend(("> " + _org_inline(item)).rstrip() if item.strip() else ">" for item in body)
            else:
                unescaped = [item[1:] if item.startswith((",*", ",#+", ",,")) else item for item in body]
                lang = (block.group(3) or "") if name == "SRC" else ""
                out.extend(_dialect_code_block_lines(unescaped, lang))
            numberer.reset()
            indents = []
            continue
        heading_match = _ORG_HEADING_RE.match(line)
        list_match = _ORG_LIST_RE.match(line)
        if heading_match:
            out.append("#" * min(len(heading_match.group(1)), 6) + " " + _org_inline(heading_match.group(2)))
            numberer.reset()
            indents = []
        elif re.match(r"^-{5,}[ \t]*$", line):
            out.append("---")
        elif list_match:
            depth = _depth_from_indent(indents, len(list_match.group(1)))
            ordered = list_match.group(2)[0].isdigit()
            out.append(numberer.prefix(depth, ordered) + _org_inline(list_match.group(3)))
        elif not line.strip():
            out.append("")
        else:
            out.append(_org_inline(line))
            numberer.reset()
            indents = []
        index += 1
    return "\n".join(out) + ("\n" if out else "")


# --- MediaWiki / Jira shared list prefixes -----------------------------------

def _wiki_list_prefix(stack: list[str], depth: int, ordered: bool) -> str:
    del stack[depth:]
    while len(stack) < depth:
        stack.append("*")
    stack.append("#" if ordered else "*")
    return "".join(stack)


def _wiki_markup(content: str, token_fn: Any, rules: dict[str, Any]) -> str:
    out: list[str] = []
    stack: list[str] = []
    for block in _lite_blocks(content):
        kind = block[0]
        if kind != "list":
            stack = []
        if kind == "blank":
            out.append("")
        elif kind == "heading":
            out.append(rules["heading"](block[1], _render_with(token_fn, _lite_inline(block[2]))))
        elif kind == "code":
            out.extend(rules["code"](block[1], block[2].split("\n") if block[2] else []))
        elif kind == "list":
            prefix = _wiki_list_prefix(stack, block[2], block[1])
            out.append(prefix + " " + _render_with(token_fn, _lite_inline(block[3])))
        elif kind == "quote":
            out.append(rules["quote"](_render_with(token_fn, _lite_inline(block[1]))))
        elif kind == "hr":
            out.append("----")
        else:
            out.append(_render_with(token_fn, _lite_inline(block[1])))
    return "\n".join(out) + ("\n" if out else "")


def _wiki_list_to_markdown(numberer: _MarkdownListNumberer, prefix: str, text: str) -> str:
    return numberer.prefix(len(prefix) - 1, prefix[-1] == "#") + text


# --- MediaWiki --------------------------------------------------------------

def _mediawiki_token(token: tuple[Any, ...]) -> str:
    kind = token[0]
    if kind == "text":
        return token[1]
    if kind == "code":
        return f"<code>{token[1]}</code>"
    if kind == "bold":
        return "'''" + _render_with(_mediawiki_token, token[1]) + "'''"
    if kind == "italic":
        return "''" + _render_with(_mediawiki_token, token[1]) + "''"
    if kind == "strike":
        return "<s>" + _render_with(_mediawiki_token, token[1]) + "</s>"
    if kind == "link":
        label = _render_with(_mediawiki_token, token[1])
        return f"[{token[2]}]" if _lite_plain(token[1]) == token[2] else f"[{token[2]} {label}]"
    if kind == "image":
        return f"[{token[2]} {token[1]}]" if token[1] else f"[{token[2]}]"
    return ""


def _mediawiki_code(lang: str, body: list[str]) -> list[str]:
    if lang:
        return [f'<syntaxhighlight lang="{html_module.escape(lang, quote=True)}">', *body, "</syntaxhighlight>"]
    return ["<pre>", *body, "</pre>"]


def markdown_to_mediawiki(content: str) -> str:
    """Convert the Markdown lite subset to MediaWiki markup.

    Level-n headings -> ``=`` x n, ``**b**`` -> ``'''b'''``, ``*i*`` ->
    ``''i''``, ``~~s~~`` -> ``<s>s</s>``, code -> ``<code>``, links ->
    ``[url label]`` (external links only), lists -> ``*`` / ``#`` prefixes
    (mixed nesting like ``*#``), fences -> ``<syntaxhighlight lang=...>`` or
    ``<pre>``, quotes -> ``<blockquote>`` per line.
    Round-trips hold for absolute (http/https/mailto) link URLs; a relative
    link is written but reads back as plain text.
    """
    return _wiki_markup(
        content,
        _mediawiki_token,
        {
            "heading": lambda level, text: f"{'=' * level} {text} {'=' * level}",
            "code": _mediawiki_code,
            "quote": lambda text: f"<blockquote>{text}</blockquote>",
        },
    )


_MEDIAWIKI_CODE_RE = re.compile(r"<(?:code|tt)>(.*?)</(?:code|tt)>")
_MEDIAWIKI_EXT_LINK_RE = re.compile(r"(?<!\[)\[((?:https?|mailto):[^\s\]]+)(?:[ \t]+([^\]\n]+))?\](?!\])")
_MEDIAWIKI_EMPHASIS = (
    (re.compile(r"'''(?!')(.+?)(?<!')'''"), "bold"),
    (re.compile(r"''(?!')(.+?)(?<!')''"), "italic"),
    (re.compile(r"<(?:s|del|strike)>(.+?)</(?:s|del|strike)>"), "strike"),
)
_MEDIAWIKI_HEADING_RE = re.compile(r"^(={1,6})[ \t]*(.+?)[ \t]*\1[ \t]*$")
_MEDIAWIKI_LIST_RE = re.compile(r"^([*#]+)[ \t]*(.*)$")
_MEDIAWIKI_CODE_OPEN_RE = re.compile(r'^[ \t]*<(syntaxhighlight|source|pre)(?:[^>]*?\blang="?([\w+#.-]+)"?)?[^>]*>[ \t]*$')
_MEDIAWIKI_QUOTE_RE = re.compile(r"^[ \t]*<blockquote>(.*)</blockquote>[ \t]*$")


def _mediawiki_link_to_markdown(match: re.Match[str]) -> str:
    url, label = match.group(1), match.group(2)
    return f"<{url}>" if label is None else f"[{_mediawiki_inline(label)}]({url})"


def _mediawiki_inline(text: str) -> str:
    return _dialect_inline_to_markdown(
        text,
        code=_MEDIAWIKI_CODE_RE,
        links=((_MEDIAWIKI_EXT_LINK_RE, _mediawiki_link_to_markdown),),
        emphasis=_MEDIAWIKI_EMPHASIS,
    )


def mediawiki_to_markdown(content: str) -> str:
    """Convert MediaWiki markup to canonical Markdown (narrow contract).

    Supports ``=`` headings, ``*``/``#`` lists, ``<syntaxhighlight>`` /
    ``<source>`` / ``<pre>`` blocks, one-line ``<blockquote>``, ``----``,
    and ``'''b'''`` / ``''i''`` / ``<s>`` / ``<code>`` / external
    ``[url label]`` links. Internal ``[[Page]]`` links, templates
    ``{{...}}``, tables ``{|...|}``, and ``<ref>`` are kept literally.
    """
    out: list[str] = []
    numberer = _MarkdownListNumberer()
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        code_open = _MEDIAWIKI_CODE_OPEN_RE.match(line)
        if code_open:
            tag = code_open.group(1)
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != f"</{tag}>":
                body.append(lines[index])
                index += 1
            index += 1
            out.extend(_dialect_code_block_lines(body, code_open.group(2) or ""))
            numberer.reset()
            continue
        heading_match = _MEDIAWIKI_HEADING_RE.match(line)
        list_match = _MEDIAWIKI_LIST_RE.match(line)
        quote_match = _MEDIAWIKI_QUOTE_RE.match(line)
        if heading_match:
            out.append("#" * len(heading_match.group(1)) + " " + _mediawiki_inline(heading_match.group(2)))
            numberer.reset()
        elif re.match(r"^-{4,}[ \t]*$", line):
            out.append("---")
            numberer.reset()
        elif list_match:
            out.append(_wiki_list_to_markdown(numberer, list_match.group(1), _mediawiki_inline(list_match.group(2))))
        elif quote_match:
            out.append(("> " + _mediawiki_inline(quote_match.group(1))).rstrip())
            numberer.reset()
        elif not line.strip():
            out.append("")
        else:
            out.append(_mediawiki_inline(line))
            numberer.reset()
        index += 1
    return "\n".join(out) + ("\n" if out else "")


# --- Jira wiki markup -------------------------------------------------------

def _jira_token(token: tuple[Any, ...]) -> str:
    kind = token[0]
    if kind == "text":
        return token[1]
    if kind == "code":
        return "{{" + token[1] + "}}"
    if kind in {"bold", "italic", "strike"}:
        mark = {"bold": "*", "italic": "_", "strike": "-"}[kind]
        return mark + _render_with(_jira_token, token[1]) + mark
    if kind == "link":
        label = _render_with(_jira_token, token[1])
        return f"[{token[2]}]" if _lite_plain(token[1]) == token[2] else f"[{label}|{token[2]}]"
    if kind == "image":
        return f"!{token[2]}!"
    return ""


def _jira_code(lang: str, body: list[str]) -> list[str]:
    return ["{code:" + lang + "}" if lang else "{code}", *body, "{code}"]


def markdown_to_jira(content: str) -> str:
    """Convert the Markdown lite subset to Jira wiki markup.

    Headings -> ``hN.``, ``**b**`` -> ``*b*``, ``*i*`` -> ``_i_``,
    ``~~s~~`` -> ``-s-``, code -> ``{{c}}``, links -> ``[label|url]``,
    images -> ``!url!``, lists -> ``*`` / ``#`` prefixes, fences ->
    ``{code:lang}``, quotes -> ``bq.`` lines.
    Round-trips hold for absolute (http/https/mailto) link URLs; a relative
    link is written but reads back as plain text.
    """
    return _wiki_markup(
        content,
        _jira_token,
        {
            "heading": lambda level, text: f"h{level}. {text}",
            "code": _jira_code,
            "quote": lambda text: f"bq. {text}",
        },
    )


_JIRA_CODE_RE = re.compile(r"\{\{(.+?)\}\}")
_JIRA_LINK_RE = re.compile(r"\[(?:([^\]|\n]+)\|)?((?:https?|mailto):[^\]\s|]+)\]")
_JIRA_IMAGE_RE = re.compile(r"!((?:https?://[^!\s|]+)|(?:[^!\s|]+\.[A-Za-z0-9]{2,5}))(?:\|[^!\n]*)?!")
_JIRA_EMPHASIS = (
    (re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])"), "bold"),
    (re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])"), "italic"),
    (re.compile(r"(?<![\w-])-(?=[^\s-])([^\n]+?)(?<=[^\s-])-(?![\w-])"), "strike"),
)
_JIRA_HEADING_RE = re.compile(r"^h([1-6])\.[ \t]+(.*?)[ \t]*$")
_JIRA_LIST_RE = re.compile(r"^([*#]+|-)[ \t]+(.*)$")
_JIRA_CODE_OPEN_RE = re.compile(r"^[ \t]*\{(code|noformat)(?::([^}]*))?\}[ \t]*$")


def _jira_link_to_markdown(match: re.Match[str]) -> str:
    label, url = match.group(1), match.group(2)
    return f"<{url}>" if label is None else f"[{_jira_inline(label)}]({url})"


def _jira_inline(text: str) -> str:
    return _dialect_inline_to_markdown(
        text,
        code=_JIRA_CODE_RE,
        links=(
            (_JIRA_LINK_RE, _jira_link_to_markdown),
            (_JIRA_IMAGE_RE, lambda m: f"![]({m.group(1)})" if _sanitize_url_scheme(m.group(1)) else m.group(1)),
        ),
        emphasis=_JIRA_EMPHASIS,
    )


def jira_to_markdown(content: str) -> str:
    """Convert Jira wiki markup to canonical Markdown (narrow contract).

    Supports ``hN.`` headings, ``*``/``#``/``-`` lists, ``{code[:lang]}`` /
    ``{noformat}`` / ``{quote}`` blocks, ``bq.`` lines, ``----``, and
    ``*b*`` / ``_i_`` / ``-s-`` / ``{{c}}`` / ``[label|url]`` / ``!img!``.
    Tables, panels, ``{color}``, mentions, and ``+u+`` / ``^sup^`` /
    ``~sub~`` are kept literally.
    """
    out: list[str] = []
    numberer = _MarkdownListNumberer()
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        code_open = _JIRA_CODE_OPEN_RE.match(line)
        if code_open or line.strip() == "{quote}":
            tag = code_open.group(1) if code_open else "quote"
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "{" + tag + "}":
                body.append(lines[index])
                index += 1
            index += 1
            if tag == "quote":
                out.extend(("> " + _jira_inline(item)).rstrip() if item.strip() else ">" for item in body)
            else:
                options = (code_open.group(2) or "") if tag == "code" else ""
                lang = options.split("|")[0]
                lang = "" if "=" in lang else lang
                for option in options.split("|"):
                    if option.startswith("language="):
                        lang = option.split("=", 1)[1]
                out.extend(_dialect_code_block_lines(body, lang))
            numberer.reset()
            continue
        heading_match = _JIRA_HEADING_RE.match(line)
        list_match = _JIRA_LIST_RE.match(line)
        if heading_match:
            out.append("#" * int(heading_match.group(1)) + " " + _jira_inline(heading_match.group(2)))
            numberer.reset()
        elif re.match(r"^-{4,}[ \t]*$", line):
            out.append("---")
            numberer.reset()
        elif list_match:
            prefix = "*" if list_match.group(1) == "-" else list_match.group(1)
            out.append(_wiki_list_to_markdown(numberer, prefix, _jira_inline(list_match.group(2))))
        elif line.startswith("bq. "):
            out.append("> " + _jira_inline(line[4:]))
            numberer.reset()
        elif not line.strip():
            out.append("")
        else:
            out.append(_jira_inline(line))
            numberer.reset()
        index += 1
    return "\n".join(out) + ("\n" if out else "")


# === SECTION: llm io ===
# LLM inputs/outputs are hard to read in logs and tests: a reasoning model
# interleaves private "thinking" with the actual answer, a full response can
# run thousands of characters, and assertions/log lines rarely need the whole
# blob verbatim. These helpers separate reasoning from the answer, compress
# an output for logs, summarize it for assertions, and find IDs (UUIDs,
# URLs, GitHub refs, hex SHAs) worth referencing instead of embedding whole
# content. They reuse the shared scanner/masking helpers, code_block's
# adaptive fence, and the extract_* family rather than re-parsing fences or
# code spans.

_REASONING_TAG_NAMES = ("think", "thinking", "reasoning")
_REASONING_OPEN_RE = re.compile(r"<(think|thinking|reasoning)\b[^>]*>", re.IGNORECASE)
_REASONING_CLOSE_RE = {
    name: re.compile(r"</" + name + r"\s*>", re.IGNORECASE) for name in _REASONING_TAG_NAMES
}
# Open WebUI streams reasoning as a collapsible <details type="reasoning">
# block with a leading <summary> line (e.g. "Thought for 5 seconds") that is
# not itself part of the reasoning content.
_REASONING_DETAILS_OPEN_RE = re.compile(
    r'<details\b(?=[^>]*\btype=["\']reasoning["\'])[^>]*>', re.IGNORECASE
)
_DETAILS_TAG_RE = re.compile(r"</?details\b[^>]*>", re.IGNORECASE)
_REASONING_SUMMARY_RE = re.compile(
    r"\A[ \t\n]*<summary\b[^>]*>.*?</summary>[ \t]*\n?",
    re.IGNORECASE | re.DOTALL,
)
_LLM_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LLM_GITHUB_REPO_REF_RE = re.compile(
    r"\b[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?#[0-9]+\b"
)
_LLM_GITHUB_BARE_REF_RE = re.compile(r"(?<![\w/])#[0-9]+\b")
_LLM_HEX_RUN_RE = re.compile(r"(?<![0-9A-Za-z_])[0-9a-fA-F]+(?![0-9A-Za-z_])")


def _mask_code_context(text: str, *, mask_inline: bool) -> str:
    """Same-length ``text`` with fenced code, and optionally inline code, blanked.

    Positions line up with ``text`` line by line, so offsets found by
    scanning the result can slice ``text`` itself unchanged. Uses the shared
    scanner contract (``_scan_lines`` for fence state, ``_mask_inline_code``
    for balanced backtick spans) instead of a new ad hoc fence tracker.
    """
    lines = text.split("\n")
    scanned = _scan_lines(lines)
    masked_lines: list[str] = []
    for line, item in zip(lines, scanned):
        if item.in_fenced_code:
            masked_lines.append(" " * len(line))
        elif mask_inline:
            masked_lines.append(_mask_inline_code(line))
        else:
            masked_lines.append(line)
    return "\n".join(masked_lines)


def _collapse_blank_runs(text: str, max_blank_lines: int) -> str:
    """Collapse runs of blank lines outside fenced code to ``max_blank_lines``."""
    lines = text.split("\n")
    scanned = _scan_lines(lines)
    out: list[str] = []
    blank_run = 0
    for line, item in zip(lines, scanned):
        is_blank = not item.in_fenced_code and line.strip() == ""
        if is_blank:
            blank_run += 1
            if blank_run > max_blank_lines:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out)


def _truncate_code_blocks(text: str, max_code_lines: int) -> str:
    """Shorten fenced code blocks over ``max_code_lines`` lines, fence-safe.

    Walks lines with the shared scanner exactly like ``extract_code_blocks``,
    but rewrites the block instead of only collecting it: kept lines are
    followed by one ``... N more lines`` marker line, re-fenced with
    ``code_block`` so its adaptive fence stays longer than any backtick run
    left in the (possibly truncated) body.
    """
    lines = text.split("\n")
    scanned = _scan_lines(lines)
    out: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        if not scanned[index].is_fence_open:
            out.append(lines[index])
            index += 1
            continue
        match = _FENCE_RE.match(lines[index])
        fence_char = match.group(1)[0] if match else "`"
        info = match.group(2).strip() if match else ""
        body: list[str] = []
        index += 1
        while index < total and not scanned[index].is_fence_close:
            body.append(lines[index])
            index += 1
        if index < total:
            index += 1  # consume the closing fence line itself
        marker = re.fullmatch(r"… (\d+) more lines", body[-1]) if body else None
        already_compacted = (
            marker is not None
            and int(marker.group(1)) > 0
            and len(body) == max_code_lines + 1
        )
        if len(body) > max_code_lines and not already_compacted:
            extra = len(body) - max_code_lines
            body = body[:max_code_lines] + [f"… {extra} more lines"]
        rendered = code_block("\n".join(body), info, fence_char=fence_char).split("\n")
        if rendered and rendered[-1] == "":
            rendered.pop()
        out.extend(rendered)
    return "\n".join(out)


def _dedupe_in_order(matches: list[tuple[int, str]]) -> list[str]:
    """Sort ``(position, value)`` pairs by position and drop repeat values."""
    seen: set[str] = set()
    ordered: list[str] = []
    for _position, value in sorted(matches, key=lambda pair: pair[0]):
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank ``text`` over each ``(start, end)`` span, keeping length/positions."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def _matching_details_close(mask: str, start: int) -> re.Match[str] | None:
    """Find the closing tag balanced with the details block opened at start."""
    depth = 1
    for match in _DETAILS_TAG_RE.finditer(mask, start):
        tag = match.group(0)
        if tag.startswith("</"):
            depth -= 1
        elif not tag.rstrip().endswith("/>"):
            depth += 1
        if depth == 0:
            return match
    return None


def _split_reasoning_raw(text: str) -> tuple[str, list[str], list[str]]:
    """Remove reasoning blocks from ``text`` with no shaping applied yet.

    Shared core for :func:`split_reasoning` and :func:`compact_llm_output`:
    returns ``(raw_answer, reasoning, formats)`` where ``raw_answer`` is
    ``text`` with every reasoning block cut out, but *not yet* blank-line
    collapsed or whitespace-stripped -- each caller applies its own final
    shaping (``split_reasoning`` always collapses to one blank line;
    ``compact_llm_output`` collapses to its caller-chosen ``max_blank_lines``).

    See :func:`split_reasoning` for the recognized tag formats and the
    unclosed-tag / fenced-code / inline-code rules.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    mask = _mask_code_context(normalized, mask_inline=True)
    length = len(normalized)

    kept: list[str] = []
    reasoning: list[str] = []
    formats: list[str] = []
    pos = 0
    while True:
        tag_match = _REASONING_OPEN_RE.search(mask, pos)
        details_match = _REASONING_DETAILS_OPEN_RE.search(mask, pos)
        candidates = [m for m in (tag_match, details_match) if m is not None]
        if not candidates:
            kept.append(normalized[pos:])
            break
        opening = min(candidates, key=lambda m: m.start())
        kept.append(normalized[pos:opening.start()])

        if opening is details_match:
            fmt = "details"
            close_match = _matching_details_close(mask, opening.end())
        else:
            fmt = opening.group(1).lower()
            close_match = _REASONING_CLOSE_RE[fmt].search(mask, opening.end())

        if close_match is None:
            block = normalized[opening.end():]
            pos = length
        else:
            block = normalized[opening.end():close_match.start()]
            pos = close_match.end()

        if fmt == "details":
            block = _REASONING_SUMMARY_RE.sub("", block, count=1)

        reasoning.append(block.strip())
        formats.append(fmt)

        if close_match is None:
            break

    return "".join(kept), reasoning, formats


def split_reasoning(text: str) -> dict[str, Any]:
    """Split reasoning-model output into its answer and reasoning blocks.

    Recognizes case-insensitive ``<think>``, ``<thinking>``, and
    ``<reasoning>`` tags, plus Open WebUI's collapsible
    ``<details type="reasoning" ...><summary>...</summary>...</details>``
    (the summary line is dropped; the rest of that block is reasoning). Tags
    inside fenced code or inline code spans are ignored and stay literal in
    the answer, using the shared scanner/masking helpers rather than a
    second fence tracker. An opening tag with no matching close (a stream
    cut mid-thought) makes the rest of the text one final reasoning block.

    Returns a dict with ``answer`` (the input with every reasoning block
    removed, blank-line runs left behind collapsed to one, and outer
    whitespace stripped -- a trailing ``"\\n"`` when non-empty, matching
    this module's other converters), ``reasoning`` (each block's stripped
    text, in order of appearance), and ``formats`` (one tag name per block,
    parallel to ``reasoning``: ``"think"``, ``"thinking"``, ``"reasoning"``,
    or ``"details"``).

    :raises ValueError: if ``text`` is not a ``str``.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not text:
        return {"answer": "", "reasoning": [], "formats": []}

    raw_answer, reasoning, formats = _split_reasoning_raw(text)
    answer = _collapse_blank_runs(raw_answer, 1).strip()
    return {
        "answer": answer + "\n" if answer else "",
        "reasoning": reasoning,
        "formats": formats,
    }


def compact_llm_output(
    text: str,
    *,
    keep_reasoning: bool = False,
    max_code_lines: int = 40,
    max_blank_lines: int = 1,
) -> str:
    """Deterministically compress LLM output for logs and test assertions.

    Drops reasoning blocks (the same recognizer as :func:`split_reasoning`)
    unless ``keep_reasoning`` is set. Blank-line runs outside fenced code
    are collapsed to ``max_blank_lines`` -- this is applied directly to the
    reasoning-stripped text rather than through ``split_reasoning``'s own
    ``answer`` field, since that field always collapses to exactly one
    blank line. A fenced code block longer than ``max_code_lines`` lines
    keeps only its first ``max_code_lines`` lines, then a final
    ``... N more lines`` marker line, re-fenced with :func:`code_block` so
    its adaptive fence stays stable even when the kept lines contain their
    own backtick runs. Code content is never otherwise altered.

    :raises ValueError: if ``text`` is not a ``str``, or ``max_code_lines``
        / ``max_blank_lines`` is negative.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if max_code_lines < 0:
        raise ValueError("max_code_lines must be >= 0")
    if max_blank_lines < 0:
        raise ValueError("max_blank_lines must be >= 0")
    if not text:
        return ""

    if keep_reasoning:
        working = text
    else:
        working, _reasoning, _formats = _split_reasoning_raw(text)
    normalized = working.replace("\r\n", "\n").replace("\r", "\n")
    collapsed = _collapse_blank_runs(normalized, max_blank_lines)
    truncated = _truncate_code_blocks(collapsed, max_code_lines)
    result = truncated.strip()
    return result + "\n" if result else ""


def llm_output_digest(text: str) -> dict[str, Any]:
    """Summarize LLM output as a small JSON-able dict for logs and assertions.

    Splits off reasoning with :func:`split_reasoning` first, then reuses the
    existing ``extract_*`` helpers on the remaining answer: ``headings``
    (:func:`extract_sections` titles), ``code_blocks``
    (:func:`extract_code_blocks` as ``{"lang", "lines"}``), and ``links``
    (:func:`extract_urls`). ``has_table`` reuses :func:`markdown_table_to_rows`
    rather than a second table sniffer.

    Returns a dict with ``answer_chars``, ``reasoning_chars``,
    ``reasoning_blocks``, ``headings``, ``code_blocks``, ``links``, and
    ``has_table``.

    :raises ValueError: if ``text`` is not a ``str``.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    split = split_reasoning(text)
    answer = split["answer"]
    headers, _rows = markdown_table_to_rows(answer)
    return {
        "answer_chars": len(answer),
        "reasoning_chars": sum(len(block) for block in split["reasoning"]),
        "reasoning_blocks": len(split["reasoning"]),
        "headings": [item["title"] for item in extract_sections(answer)],
        "code_blocks": [
            {"lang": block["language"], "lines": len(block["code"].splitlines())}
            for block in extract_code_blocks(answer)
        ],
        "links": extract_urls(answer),
        "has_table": bool(headers),
    }


def extract_identifiers(text: str) -> dict[str, Any]:
    """Find IDs worth referencing instead of embedding whole content.

    Looks for UUIDs, URLs (:func:`extract_urls`), GitHub issue/PR
    references (``owner/repo#123`` and bare ``#123``), and bare hex "SHA"
    runs (7-40 hex characters containing at least one letter and one digit,
    and not part of an already-matched UUID or URL). Each list is
    deduplicated in order of first appearance. Matches inside fenced code
    are skipped, using the shared scanner rather than a second fence
    tracker; inline code is scanned normally.

    Returns a dict with ``uuids``, ``urls``, ``github_refs``, and ``shas``.

    :raises ValueError: if ``text`` is not a ``str``.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not text:
        return {"uuids": [], "urls": [], "github_refs": [], "shas": []}

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = _mask_code_context(normalized, mask_inline=False)

    uuid_hits = [(m.start(), m.end(), m.group(0)) for m in _LLM_UUID_RE.finditer(masked)]
    uuids = _dedupe_in_order([(start, value) for start, _end, value in uuid_hits])

    urls = extract_urls(masked)

    repo_hits = [(m.start(), m.group(0)) for m in _LLM_GITHUB_REPO_REF_RE.finditer(masked)]
    bare_hits = [(m.start(), m.group(0)) for m in _LLM_GITHUB_BARE_REF_RE.finditer(masked)]
    github_refs = _dedupe_in_order(repo_hits + bare_hits)

    # A SHA must not be carved out of an already-matched UUID or URL, so
    # blank both before scanning for bare hex runs.
    exclude_spans = [(start, end) for start, end, _value in uuid_hits]
    for url in urls:
        search_from = 0
        while True:
            found = masked.find(url, search_from)
            if found < 0:
                break
            exclude_spans.append((found, found + len(url)))
            search_from = found + len(url)
    sha_source = _mask_spans(masked, exclude_spans)

    sha_hits: list[tuple[int, str]] = []
    for m in _LLM_HEX_RUN_RE.finditer(sha_source):
        run = m.group(0)
        if 7 <= len(run) <= 40 and any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            sha_hits.append((m.start(), run))
    shas = _dedupe_in_order(sha_hits)

    return {"uuids": uuids, "urls": urls, "github_refs": github_refs, "shas": shas}


def _main(argv=None) -> int:
    """Small stdio entry point for uv tools; library functions remain reusable."""
    parser = argparse.ArgumentParser(description="Stdlib-only Markdown helpers")
    parser.add_argument("operation", nargs="?", default="inventory",
                        choices=("inventory", "to-html", "to-markdown", "toc"),help="what to do with the input (default: %(default)s)")
    parser.add_argument("path", nargs="?", default="-", help="UTF-8 input file, or - for stdin")
    args = parser.parse_args(argv)
    try:
        content = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
        if args.operation == "inventory":
            output = json.dumps(inventory(content), ensure_ascii=False, indent=2)
        else:
            transform = {"to-html": markdown_to_html, "to-markdown": html_to_markdown, "toc": table_of_contents}[args.operation]
            output = transform(content)
        print(output, end="" if output.endswith("\n") else "\n")
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
