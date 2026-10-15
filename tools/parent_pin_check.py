#!/usr/bin/env python3
"""Scan a checkout for commit pins to the parent repository (stdlib only)."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PARENT_REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SCHEMA = "parent-pin-check/1"
MAX_BYTES = 8 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".test-deps"}
ZERO_SHA = "0" * 40

DEFAULT_EXCLUDE_GLOBS = (
    "child-heads.json",
    "vendor-catalog.json",
    "vendor-consumers.json",
    "docs/vendor-*.json",
    "docs/vendor-target-inventory.json",
)

USES_PARENT = re.compile(
    r"uses:\s*['\"]?"
    + re.escape(PARENT_REPOSITORY)
    + r"/(?P<entry>[^\s@'\"]+)@(?P<sha>[0-9a-f]{40})\b"
)
CHECKOUT_REPO = re.compile(
    r"^\s+repository:\s*['\"]?" + re.escape(PARENT_REPOSITORY) + r"['\"]?\s*$"
)
CHECKOUT_REF = re.compile(r"^\s+ref:\s*['\"]?(?P<sha>[0-9a-f]{40})['\"]?\s*$")
INLINE_PARENT_SHA = re.compile(
    re.escape(PARENT_REPOSITORY) + r"/[^\s@'\"]+@(?P<sha>[0-9a-f]{40})\b"
)
PIN_CONSTANT = re.compile(
    r"(?P<name>(?:VENDOR_SYNC_PIN|PARENT_[A-Z0-9_]*PIN|[A-Z0-9_]*_PARENT_PIN))\s*=\s*['\"](?P<sha>[0-9a-f]{40})['\"]"
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ParentPinError(RuntimeError):
    """Fail-closed configuration or GitHub API failure."""


def _normalize_repo(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().strip("'\"")
    if text.endswith(".git"):
        text = text[:-4]
    if "github.com/" in text:
        text = text.split("github.com/", 1)[1]
    text = text.strip("/")
    if text.count("/") != 1:
        return None
    owner, name = text.split("/", 1)
    if not owner or not name:
        return None
    return f"{owner}/{name}"


def _is_parent(name: str | None, parent_repository: str) -> bool:
    return bool(name) and name.casefold() == parent_repository.casefold()


def _excluded(rel: str, patterns: Sequence[str]) -> bool:
    normalized = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _github_json(url: str, token: str | None, *, timeout: float = 60.0) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "parent-pin-check",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        raise ParentPinError(
            f"GitHub API request failed ({error.code}) while resolving parent default branch; "
            "set GITHUB_TOKEN and ensure the parent repository is public."
        ) from error
    except URLError as error:
        raise ParentPinError(
            "GitHub API is unreachable while resolving parent default branch; fail-closed."
        ) from error
    if len(raw) > MAX_BYTES:
        raise ParentPinError("GitHub API response exceeds size limit.")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ParentPinError("GitHub API returned an unexpected payload.")
    return data


def fetch_parent_main_sha(parent_repository: str, token: str | None) -> tuple[str, str]:
    meta = _github_json(f"https://api.github.com/repos/{parent_repository}", token)
    visibility = meta.get("visibility") or ("private" if meta.get("private") else "public")
    if visibility != "public" or meta.get("private"):
        raise ParentPinError("Parent repository is not public; refusing to proceed (fail-closed).")
    if meta.get("archived"):
        raise ParentPinError("Parent repository is archived; refusing to proceed (fail-closed).")
    branch = meta.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ParentPinError("Parent repository default branch is missing from GitHub API response.")
    ref = _github_json(
        f"https://api.github.com/repos/{parent_repository}/git/ref/heads/"
        + quote(branch, safe=""),
        token,
    )
    object_info = ref.get("object") if isinstance(ref.get("object"), dict) else {}
    sha = object_info.get("sha")
    if not isinstance(sha, str) or not HEX40.fullmatch(sha):
        raise ParentPinError("Could not resolve parent default branch HEAD SHA.")
    return sha, branch


def pin_behind_main(parent_repository: str, pin: str, main_sha: str, token: str | None) -> bool:
    if pin == main_sha:
        return False
    url = (
        f"https://api.github.com/repos/{parent_repository}/compare/"
        f"{quote(pin, safe='')}...{quote(main_sha, safe='')}"
    )
    data = _github_json(url, token)
    status = data.get("status")
    if status == "identical":
        return False
    if status in ("ahead", "behind", "diverged"):
        ahead = int(data.get("ahead_by") or 0)
        behind = int(data.get("behind_by") or 0)
        if ahead > 0 and behind == 0:
            return True
        if status == "diverged":
            raise ParentPinError(
                "Pinned parent commit is not an ancestor of default branch HEAD (diverged)."
            )
        if behind > 0:
            raise ParentPinError(
                "Pinned parent commit is ahead of default branch HEAD; review the pin manually."
            )
    return False


def _append_pin(
    pins: list[dict[str, Any]],
    *,
    sha: str,
    file: str,
    line: int | None,
    kind: str,
    entry: str = "",
) -> None:
    if not HEX40.fullmatch(sha) or sha == ZERO_SHA:
        return
    pins.append(
        {
            "commit": sha,
            "file": file,
            "line": line,
            "kind": kind,
            "entry": entry,
        }
    )


def collect_workflow_pins(path: str, text: str, parent_repository: str) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines, 1):
        match = USES_PARENT.search(line)
        if match:
            _append_pin(
                pins,
                sha=match.group("sha"),
                file=path,
                line=index,
                kind="workflow-uses",
                entry=match.group("entry"),
            )
    for index, line in enumerate(lines, 1):
        if not CHECKOUT_REPO.match(line):
            continue
        for offset in range(1, min(12, len(lines) - index + 1)):
            ref_match = CHECKOUT_REF.match(lines[index + offset - 1])
            if ref_match:
                _append_pin(
                    pins,
                    sha=ref_match.group("sha"),
                    file=path,
                    line=index + offset,
                    kind="workflow-checkout",
                    entry="actions/checkout",
                )
                break
    return pins


def collect_text_pins(path: str, text: str, parent_repository: str) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        for match in INLINE_PARENT_SHA.finditer(line):
            _append_pin(
                pins,
                sha=match.group("sha"),
                file=path,
                line=index,
                kind="text-reference",
                entry="inline",
            )
        const = PIN_CONSTANT.search(line)
        if const:
            _append_pin(
                pins,
                sha=const.group("sha"),
                file=path,
                line=index,
                kind="pin-constant",
                entry=const.group("name"),
            )
    return pins


def _json_parent_pins(path: str, document: Any, parent_repository: str) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []

    def walk(node: Any, context_repo: str | None = None) -> None:
        if isinstance(node, dict):
            repo = _normalize_repo(node.get("repository") or node.get("repo") or context_repo or "")
            if _is_parent(repo, parent_repository):
                for key in ("commit", "sha"):
                    value = node.get(key)
                    if isinstance(value, str) and HEX40.fullmatch(value):
                        _append_pin(
                            pins,
                            sha=value,
                            file=path,
                            line=None,
                            kind=f"json-{key}",
                            entry=str(node.get("source") or node.get("destination") or key),
                        )
            for value in node.values():
                walk(value, repo if _is_parent(repo, parent_repository) else context_repo)
        elif isinstance(node, list):
            for value in node:
                walk(value, context_repo)

    walk(document)
    return pins


def scan(
    root: str | Path,
    *,
    parent_repository: str = PARENT_REPOSITORY,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    patterns = list(exclude_globs)
    if include_default_excludes:
        patterns = list(DEFAULT_EXCLUDE_GLOBS) + list(exclude_globs)
    pins: list[dict[str, Any]] = []
    for directory, dirs, files in os.walk(root_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = Path(directory) / name
            if full.is_symlink():
                continue
            rel = os.path.relpath(full, root_path).replace(os.sep, "/")
            if _excluded(rel, patterns):
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_BYTES:
                continue
            suffix = full.suffix.lower()
            try:
                if rel.startswith(".github/workflows/") and suffix in (".yml", ".yaml"):
                    text = full.read_text(encoding="utf-8")
                    pins.extend(collect_workflow_pins(rel, text, parent_repository))
                    pins.extend(collect_text_pins(rel, text, parent_repository))
                elif suffix == ".json":
                    document = json.loads(full.read_text(encoding="utf-8"))
                    pins.extend(_json_parent_pins(rel, document, parent_repository))
                elif suffix in (".md", ".py", ".yml", ".yaml", ".toml", ".txt"):
                    text = full.read_text(encoding="utf-8")
                    pins.extend(collect_text_pins(rel, text, parent_repository))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    return pins


def evaluate(
    pins: Sequence[dict[str, Any]],
    *,
    parent_repository: str,
    main_sha: str | None,
    token: str | None,
    fail_on_stale: bool,
) -> dict[str, Any]:
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for pin in pins:
        by_sha.setdefault(pin["commit"], []).append(pin)
    unique = sorted(by_sha)
    inconsistent = len(unique) > 1
    stale = False
    stale_shas: list[str] = []
    if main_sha and unique and token and not inconsistent:
        for sha in unique:
            if sha == main_sha:
                continue
            if pin_behind_main(parent_repository, sha, main_sha, token):
                stale = True
                stale_shas.append(sha)
    fail = inconsistent or (fail_on_stale and stale)
    return {
        "schema": SCHEMA,
        "parent_repository": parent_repository,
        "main_sha": main_sha,
        "pins": list(pins),
        "unique_commits": unique,
        "inconsistent": inconsistent,
        "stale": stale,
        "stale_commits": stale_shas,
        "fail_on_stale": fail_on_stale,
        "ok": not fail,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"parent-pin-check: {len(report['pins'])} pin(s), "
        f"{len(report['unique_commits'])} unique commit(s)"
    ]
    if report["unique_commits"]:
        lines.append("unique: " + ", ".join(report["unique_commits"]))
    for pin in report["pins"]:
        line = "" if pin.get("line") is None else f":{pin['line']}"
        lines.append(
            f"  {pin['commit']} {pin['file']}{line} ({pin['kind']})"
            + (f" {pin['entry']}" if pin.get("entry") else "")
        )
    if report["inconsistent"]:
        lines.append("ERROR: parent pin values are not unified across the checkout.")
    if report["stale"]:
        lines.append(
            "WARNING: pinned commit(s) are behind parent default branch HEAD: "
            + ", ".join(report["stale_commits"])
        )
    if report.get("main_sha"):
        lines.append(f"parent main: {report['main_sha']}")
    return "\n".join(lines) + "\n"


def emit_annotations(report: dict[str, Any]) -> None:
    if report["inconsistent"]:
        for sha, group in _group_by_sha(report["pins"]).items():
            if len(report["unique_commits"]) <= 1:
                break
            for pin in group:
                line = pin.get("line") or 1
                print(
                    f"::error file={pin['file']},line={line}::"
                    f"parent pin mismatch ({sha}); expected a single unified parent SHA"
                )
    if report["stale"]:
        for sha in report["stale_commits"]:
            print(f"::warning::parent pin {sha} is behind {report['parent_repository']} main")


def _group_by_sha(pins: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pin in pins:
        grouped.setdefault(pin["commit"], []).append(pin)
    return grouped


def run(
    root: str | Path,
    *,
    parent_repository: str = PARENT_REPOSITORY,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
    token: str | None = None,
    skip_api: bool = False,
    fail_on_stale: bool = False,
) -> dict[str, Any]:
    pins = scan(
        root,
        parent_repository=parent_repository,
        exclude_globs=exclude_globs,
        include_default_excludes=include_default_excludes,
    )
    main_sha = None
    if not skip_api:
        if not token:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise ParentPinError(
                "GITHUB_TOKEN (or GH_TOKEN) is required to resolve parent main SHA (fail-closed)."
            )
        main_sha, _branch = fetch_parent_main_sha(parent_repository, token)
    report = evaluate(
        pins,
        parent_repository=parent_repository,
        main_sha=main_sha,
        token=token,
        fail_on_stale=fail_on_stale,
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Checkout root to scan")
    parser.add_argument("--parent", default=PARENT_REPOSITORY, help="Parent OWNER/REPO")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Additional path globs to skip (fnmatch, checkout-relative)",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Do not apply built-in fleet observation excludes",
    )
    parser.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="Exit non-zero when pins are behind parent main (default: warn only)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report on stdout")
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Offline mode: skip parent main resolution and stale checks",
    )
    args = parser.parse_args(argv)
    try:
        report = run(
            args.root,
            parent_repository=args.parent,
            exclude_globs=args.exclude,
            include_default_excludes=not args.no_default_excludes,
            fail_on_stale=args.fail_on_stale,
            skip_api=args.skip_api,
        )
    except ParentPinError as error:
        print(f"::error::{error}", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return 2
    emit_annotations(report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_text(report))
    if not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
