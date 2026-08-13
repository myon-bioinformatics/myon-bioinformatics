#!/usr/bin/env python3
"""Build api/repos.json: public repo metadata + README heading/summary.

Usage:
    python3 tools/build_repos_api.py [--owner OWNER] [--output PATH]

Env:
    GITHUB_TOKEN  Optional. Used as a bearer token to raise the GitHub API
                  rate limit (1,000/h in Actions vs. 60/h unauthenticated).
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_ROOT = "https://api.github.com"
SUMMARY_LIMIT = 300

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def _headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "repos-api-builder",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def fetch_all_repos(owner):
    repos = []
    page = 1
    while True:
        url = f"{API_ROOT}/users/{owner}/repos?per_page=100&page={page}&type=owner"
        data = _get_json(url)
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def fetch_readme(owner, name):
    """Return (markdown_text, raw_download_url) or (None, None) if absent."""
    url = f"{API_ROOT}/repos/{owner}/{name}/readme"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise

    if payload.get("encoding") != "base64":
        return None, None
    text = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    return text, payload.get("download_url")


def _is_noise_line(line):
    """Badge rows, bare images, and HTML-only wrappers around them."""
    stripped = line.strip()
    if HR_RE.match(stripped):
        return True
    if stripped.startswith("|"):
        return True
    without_media = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", stripped)
    without_media = re.sub(r"!\[.*?\]\(.*?\)", "", without_media)
    without_html = re.sub(r"<[^>]+>", "", without_media)
    return without_html.strip() == ""


def extract_heading_and_summary(readme_text):
    if not readme_text:
        return None, None

    lines = readme_text.splitlines()

    heading = None
    heading_idx = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            heading = m.group(2).strip()
            heading_idx = i
            break

    if heading is None:
        return None, None

    paragraph = []
    in_code_block = False
    i = heading_idx + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1

        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if HEADING_RE.match(line):
            break
        if _is_noise_line(line):
            continue

        paragraph.append(stripped.lstrip("> ").strip())

    if not paragraph:
        return heading, None

    summary = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
    if not summary:
        return heading, None
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[:SUMMARY_LIMIT].rstrip() + "…"
    return heading, summary


def build(owner):
    entries = []
    for repo in fetch_all_repos(owner):
        if repo.get("fork"):
            continue

        name = repo["name"]
        readme_text, readme_url = fetch_readme(owner, name)
        heading, summary = extract_heading_and_summary(readme_text)

        entry = {
            "name": name,
            "url": repo.get("html_url"),
            "description": repo.get("description"),
            "language": repo.get("language"),
            "topics": repo.get("topics", []),
            "stars": repo.get("stargazers_count", 0),
            "updatedAt": repo.get("updated_at"),
            "homepage": repo.get("homepage") or None,
            "readmeHeading": heading,
            "readmeSummary": summary,
            "readmeUrl": readme_url,
        }
        if repo.get("archived"):
            entry["archived"] = True

        entries.append(entry)

    entries.sort(key=lambda r: r["name"].lower())

    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner": owner,
        "count": len(entries),
        "repos": entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="myon-bioinformatics")
    parser.add_argument("--output", default="public/api/repos.json")
    args = parser.parse_args()

    result = build(args.owner)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {args.output}: {result['count']} repos", file=sys.stderr)


if __name__ == "__main__":
    main()
