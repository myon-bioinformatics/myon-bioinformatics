#!/usr/bin/env python3
"""Scan a checkout for commit pins to the parent repository (stdlib only)."""
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PARENT_REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
PARENT_PIN_TOOL_PATH = "tools/parent_pin_check.py"
ORG_OWNER = "myon-bioinformatics"
SCHEMA = "parent-pin-check/1"
FLEET_SCHEMA = "parent-pin-fleet/1"
FLEET_PATH_PREFIXES = (".github/", "templates/")
MAX_BYTES = 8 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".test-deps"}
ZERO_SHA = "0" * 40

DEFAULT_EXCLUDE_GLOBS = (
    "child-heads.json",
    "vendor-catalog.json",
    "vendor-consumers.json",
    "docs/vendor-*.json",
    "docs/vendor-target-inventory.json",
    "**/parent-pr.ref.json",
    "parent-pr.ref.json",
    "templates/parent-pr.ref.json",
)

PARENT_PR_REF_NAME = "parent-pr.ref.json"

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
ATOM_COMMIT_SHA = re.compile(r"Commit/([0-9a-f]{40})", re.IGNORECASE)
ATOM_HTML_COMMIT = re.compile(r"/commit/([0-9a-f]{40})", re.IGNORECASE)


class ParentPinError(RuntimeError):
    """Fail-closed configuration or GitHub API failure."""

    def __init__(self, message: str, *, http_code: int | None = None) -> None:
        super().__init__(message)
        self.http_code = http_code


def _redact_fleet_child_name(error: ParentPinError) -> bool:
    message = str(error).casefold()
    if error.http_code == 404:
        return True
    return "not public" in message or "private" in message or "archived" in message


def _classify_fleet_child_error(error: ParentPinError) -> str:
    """Classify per-child fleet fetch failures: skip, warn, or fail."""
    message = str(error).casefold()
    if error.http_code == 404:
        return "skip"
    if "not public" in message or "private" in message or "archived" in message:
        return "skip"
    if error.http_code in (401, 403):
        return "warn"
    return "fail"


def _fleet_auth_warning_message(error: ParentPinError) -> str:
    if error.http_code == 401:
        return (
            "GitHub API returned HTTP 401 while fetching a child repository; "
            "check GITHUB_TOKEN authentication and permissions."
        )
    if error.http_code == 403:
        return (
            "GitHub API returned HTTP 403 while fetching a child repository; "
            "may be rate limiting or insufficient token scope."
        )
    return str(error)


def should_enforce(
    *,
    event_name: str | None = None,
    ref: str | None = None,
    enforce: bool | None = None,
) -> bool:
    """Return True when stale/unified-pin findings should fail CI (not warn only)."""
    if enforce is not None:
        return enforce
    event_name = (event_name or os.environ.get("GITHUB_EVENT_NAME") or "").strip()
    ref = (ref or os.environ.get("GITHUB_REF") or "").strip()
    if event_name == "schedule":
        return True
    if event_name == "push" and ref == "refs/heads/main":
        return True
    return False


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


def _is_parent_pr_ref_path(rel: str) -> bool:
    normalized = rel.replace(os.sep, "/")
    return normalized == PARENT_PR_REF_NAME or normalized.endswith("/" + PARENT_PR_REF_NAME)


def _main_atom_url(repository: str, branch: str = "main") -> str:
    return f"https://github.com/{repository}/commits/{quote(branch, safe='')}.atom"


def _raw_file_url(repository: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{quote(ref, safe='')}/{path}"


def _parse_first_atom_entry_commit(atom_xml: str) -> str:
    start = atom_xml.find("<entry>")
    if start < 0:
        raise ParentPinError("Atom feed has no commit entries.")
    end = atom_xml.find("</entry>", start)
    entry = atom_xml[start:end] if end > start else atom_xml[start:]
    match = ATOM_COMMIT_SHA.search(entry) or ATOM_HTML_COMMIT.search(entry)
    if not match:
        raise ParentPinError("Could not parse commit SHA from Atom feed entry.")
    return match.group(1).lower()


def _http_get_bytes(
    url: str,
    *,
    token: str | None = None,
    accept: str | None = None,
    timeout: float = 60.0,
) -> bytes:
    headers: dict[str, str] = {"User-Agent": "parent-pin-check"}
    if accept:
        headers["Accept"] = accept
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        raise ParentPinError(
            f"HTTP request failed ({error.code}).",
            http_code=error.code,
        ) from error
    except URLError as error:
        raise ParentPinError("HTTP endpoint is unreachable (fail-closed).") from error
    if len(raw) > MAX_BYTES:
        raise ParentPinError("HTTP response exceeds size limit.")
    return raw


def _http_get_text(
    url: str,
    *,
    token: str | None = None,
    accept: str | None = None,
    timeout: float = 60.0,
) -> str:
    return _http_get_bytes(url, token=token, accept=accept, timeout=timeout).decode("utf-8")


def _github_json(
    url: str, token: str | None = None, *, timeout: float = 60.0
) -> dict[str, Any]:
    raw = _http_get_bytes(
        url, token=token, accept="application/vnd.github+json", timeout=timeout
    )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ParentPinError("GitHub API returned an invalid payload.") from error
    if not isinstance(data, dict):
        raise ParentPinError("GitHub API returned an unexpected payload.")
    return data


def _github_json_list(
    url: str, token: str | None = None, *, timeout: float = 60.0
) -> list[Any]:
    raw = _http_get_bytes(
        url, token=token, accept="application/vnd.github+json", timeout=timeout
    )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ParentPinError("GitHub API returned an invalid list payload.") from error
    if not isinstance(data, list):
        raise ParentPinError("GitHub API returned an unexpected list payload.")
    return data


def fetch_parent_main_sha(parent_repository: str, token: str | None = None) -> tuple[str, str]:
    """Resolve parent default-branch HEAD from public commits/main.atom (no authentication)."""
    _ = token
    branch = "main"
    atom = _http_get_text(
        _main_atom_url(parent_repository, branch),
        token=None,
        accept="application/atom+xml, application/xml, text/xml, */*",
    )
    sha = _parse_first_atom_entry_commit(atom)
    if not HEX40.fullmatch(sha):
        raise ParentPinError("Could not resolve parent default branch HEAD SHA from Atom feed.")
    return sha, branch


_COMPARE_CACHE: dict[tuple[str, str, str], bool] = {}


def pin_behind_main(parent_repository: str, pin: str, main_sha: str, token: str | None) -> bool:
    cache_key = (parent_repository.casefold(), pin, main_sha)
    cached = _COMPARE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if pin == main_sha:
        _COMPARE_CACHE[cache_key] = False
        return False
    url = (
        f"https://api.github.com/repos/{parent_repository}/compare/"
        f"{quote(pin, safe='')}...{quote(main_sha, safe='')}"
    )
    data = _github_json(url, None)
    status = data.get("status")
    stale = False
    if status == "identical":
        stale = False
    elif status in ("ahead", "behind", "diverged"):
        ahead = int(data.get("ahead_by") or 0)
        behind = int(data.get("behind_by") or 0)
        if ahead == 0 and behind == 0:
            raise ParentPinError(
                "Unexpected GitHub compare result: zero ahead_by and behind_by "
                f"(status={status!r})."
            )
        if ahead > 0 and behind == 0:
            stale = True
        elif status == "diverged":
            raise ParentPinError(
                "Pinned parent commit is not an ancestor of default branch HEAD (diverged)."
            )
        elif behind > 0:
            raise ParentPinError(
                "Pinned parent commit is ahead of default branch HEAD; review the pin manually."
            )
    else:
        raise ParentPinError(
            f"Unexpected GitHub compare status for parent pin freshness: {status!r}"
        )
    _COMPARE_CACHE[cache_key] = stale
    return stale


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


def scan_text_files(
    files: dict[str, str],
    *,
    parent_repository: str = PARENT_REPOSITORY,
) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for rel in sorted(files):
        if _is_parent_pr_ref_path(rel):
            continue
        text = files[rel]
        suffix = Path(rel).suffix.lower()
        if rel.startswith(".github/workflows/") and suffix in (".yml", ".yaml"):
            pins.extend(collect_workflow_pins(rel, text, parent_repository))
            pins.extend(collect_text_pins(rel, text, parent_repository))
        elif suffix == ".json":
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                continue
            pins.extend(_json_parent_pins(rel, document, parent_repository))
        elif suffix in (".md", ".py", ".yml", ".yaml", ".toml", ".txt"):
            pins.extend(collect_text_pins(rel, text, parent_repository))
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
    collected: dict[str, str] = {}
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
            scannable = (
                (rel.startswith(".github/workflows/") and suffix in (".yml", ".yaml"))
                or suffix == ".json"
                or suffix in (".md", ".py", ".yml", ".yaml", ".toml", ".txt")
            )
            if not scannable:
                continue
            try:
                collected[rel] = full.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
    return scan_text_files(collected, parent_repository=parent_repository)


def evaluate(
    pins: Sequence[dict[str, Any]],
    *,
    parent_repository: str,
    main_sha: str | None,
    token: str | None,
    enforce: bool = False,
) -> dict[str, Any]:
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for pin in pins:
        by_sha.setdefault(pin["commit"], []).append(pin)
    unique = sorted(by_sha)
    inconsistent = len(unique) > 1
    stale = False
    stale_shas: list[str] = []
    if main_sha and unique and not inconsistent:
        for sha in unique:
            if sha == main_sha:
                continue
            if pin_behind_main(parent_repository, sha, main_sha, None):
                stale = True
                stale_shas.append(sha)
    findings = inconsistent or stale
    fail = enforce and findings
    return {
        "schema": SCHEMA,
        "parent_repository": parent_repository,
        "main_sha": main_sha,
        "pins": list(pins),
        "unique_commits": unique,
        "inconsistent": inconsistent,
        "stale": stale,
        "stale_commits": stale_shas,
        "enforce": enforce,
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
    level = "ERROR" if report.get("enforce") else "WARNING"
    if report["inconsistent"]:
        lines.append(f"{level}: parent pin values are not unified across the checkout.")
    if report["stale"]:
        lines.append(
            f"{level}: pinned commit(s) are behind parent default branch HEAD: "
            + ", ".join(report["stale_commits"])
        )
    if report.get("main_sha"):
        lines.append(f"parent main: {report['main_sha']}")
    return "\n".join(lines) + "\n"


def emit_annotations(report: dict[str, Any]) -> None:
    level = "error" if report.get("enforce") else "warning"
    if report["inconsistent"]:
        for sha, group in _group_by_sha(report["pins"]).items():
            if len(report["unique_commits"]) <= 1:
                break
            for pin in group:
                line = pin.get("line") or 1
                print(
                    f"::{level} file={pin['file']},line={line}::"
                    f"parent pin mismatch ({sha}); expected a single unified parent SHA"
                )
    if report["stale"]:
        for sha in report["stale_commits"]:
            print(
                f"::{level}::parent pin {sha} is behind {report['parent_repository']} main"
            )


def _group_by_sha(pins: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pin in pins:
        grouped.setdefault(pin["commit"], []).append(pin)
    return grouped


def _decode_file_content(data: dict[str, Any]) -> str:
    if data.get("type") != "file" or data.get("encoding") != "base64":
        raise ParentPinError("unexpected GitHub file payload")
    raw = base64.b64decode("".join(str(data.get("content", "")).split()), validate=True)
    if len(raw) > MAX_BYTES:
        raise ParentPinError("remote file exceeds size limit")
    return raw.decode("utf-8")


def _fetch_repository_file(
    repository: str, path: str, ref: str, token: str | None
) -> str | None:
    """Fetch a repository file from raw.githubusercontent.com (no authentication)."""
    _ = token
    url = _raw_file_url(repository, ref, path)
    try:
        return _http_get_text(url, token=None)
    except ParentPinError as error:
        if error.http_code == 404:
            return None
        raise


def _resolve_default_branch_head(repository: str, token: str | None) -> tuple[str, str]:
    """Recheck public eligibility, then resolve the actual default branch HEAD."""
    _ = token
    metadata = _github_json(f"https://api.github.com/repos/{repository}", None)
    if metadata.get("private") is True or metadata.get("visibility") in ("private", "internal"):
        raise ParentPinError("repository is not public")
    if metadata.get("archived") is True:
        raise ParentPinError("repository is archived")
    if (
        metadata.get("private") is not False
        or metadata.get("visibility") != "public"
        or metadata.get("archived") is not False
    ):
        raise ParentPinError("repository eligibility metadata is incomplete")
    branch = metadata.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ParentPinError("repository default branch missing")
    try:
        atom = _http_get_text(
            _main_atom_url(repository, branch),
            token=None,
            accept="application/atom+xml, application/xml, text/xml, */*",
        )
    except ParentPinError as error:
        if error.http_code == 404:
            raise ParentPinError("repository is not public", http_code=404) from error
        raise
    sha = _parse_first_atom_entry_commit(atom)
    if not HEX40.fullmatch(sha):
        raise ParentPinError("could not resolve default branch head")
    return sha, branch


def _list_fleet_child_repositories(
    owner: str,
    parent_repository: str,
    token: str | None,
    child_heads_path: Path | None,
    *,
    coverage: dict[str, Any] | None = None,
) -> list[str]:
    """Cross-check known HEADs against a mandatory, complete public inventory.

    The registry remains a baseline input, never a ceiling on fleet scope.
    Names absent from the public inventory are only counted, never disclosed.
    """
    heads: dict[str, Any] = {}
    if child_heads_path is not None and child_heads_path.is_file():
        try:
            document = json.loads(child_heads_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ParentPinError("could not read child heads registry") from error
        heads = document.get("heads") if isinstance(document, dict) else None
        if not isinstance(heads, dict):
            raise ParentPinError("child-heads.json is missing a heads object")
    children = _list_public_child_repositories(owner, parent_repository, token)
    known = {name.casefold() for name in heads if isinstance(name, str)}
    known.discard(parent_repository.casefold())
    public = {name.casefold() for name in children}
    if coverage is not None:
        coverage.update(
            registry_present=child_heads_path is not None and child_heads_path.is_file(),
            registered_repositories=len(public & known),
            registry_missing_repositories=[
                name for name in children if name.casefold() not in known
            ],
            registry_unmatched_count=len(known - public),
        )
    return children


def _list_public_child_repositories(
    owner: str, parent_repository: str, token: str | None
) -> list[str]:
    names: dict[str, str] = {}
    for kind in ("orgs", "users"):
        page = 1
        while page <= 20:
            url = (
                f"https://api.github.com/{kind}/{owner}/repos?per_page=100&type=public&page={page}"
            )
            try:
                data = _github_json_list(url, token)
            except ParentPinError as error:
                if kind == "orgs" and page == 1 and error.http_code == 404:
                    break
                if token and error.http_code in (401, 403):
                    # Public inventory can be read anonymously when a scoped
                    # Actions token cannot list the account's repositories.
                    try:
                        data = _github_json_list(url, None)
                    except ParentPinError as retry_error:
                        if kind == "orgs" and page == 1 and retry_error.http_code == 404:
                            break
                        raise ParentPinError(
                            "public repository inventory unavailable (incomplete fleet)",
                            http_code=retry_error.http_code,
                        ) from retry_error
                else:
                    raise ParentPinError(
                        "public repository inventory unavailable (incomplete fleet)",
                        http_code=error.http_code,
                    ) from error
            for item in data:
                if not isinstance(item, dict):
                    raise ParentPinError("public repository inventory contains invalid metadata")
                if item.get("private") is True or item.get("archived") is True:
                    continue
                if item.get("visibility") in ("private", "internal"):
                    continue
                if (
                    item.get("private") is not False
                    or item.get("visibility") != "public"
                    or item.get("archived") is not False
                ):
                    raise ParentPinError("public repository inventory eligibility metadata is incomplete")
                full_name = item.get("full_name")
                if not isinstance(full_name, str) or _normalize_repo(full_name) != full_name:
                    raise ParentPinError("public repository inventory contains an invalid repository name")
                if full_name.casefold() == parent_repository.casefold():
                    continue
                if full_name.split("/")[0].casefold() != owner.casefold():
                    continue
                names[full_name.casefold()] = full_name
            if len(data) < 100:
                return sorted(names.values(), key=str.casefold)
            page += 1
        else:
            raise ParentPinError("public repository inventory pagination limit reached (incomplete fleet)")
    raise ParentPinError("public repository inventory unavailable (incomplete fleet)")


def _remote_scannable_paths(
    repository: str,
    ref: str,
    token: str | None,
    patterns: Sequence[str],
    *,
    fleet: bool = False,
) -> list[str]:
    _ = token
    commit = _github_json(
        "https://api.github.com/repos/"
        + repository
        + "/commits/"
        + quote(ref, safe=""),
        None,
    )
    tree_sha = (commit.get("commit") or {}).get("tree", {}).get("sha")
    if not isinstance(tree_sha, str):
        raise ParentPinError("commit tree missing")
    tree = _github_json(
        f"https://api.github.com/repos/{repository}/git/trees/{tree_sha}?recursive=1",
        None,
    )
    if tree.get("truncated"):
        raise ParentPinError("repository tree listing truncated")
    paths: list[str] = []
    for item in tree.get("tree", []):
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if any(part in path.split("/") for part in SKIP_DIRS):
            continue
        if fleet:
            if not path.startswith(FLEET_PATH_PREFIXES):
                continue
        elif not (
            path.startswith(FLEET_PATH_PREFIXES)
            or ("/" not in path and Path(path).suffix)
        ):
            continue
        if _excluded(path, patterns):
            continue
        size = int(item.get("size") or 0)
        if size > MAX_BYTES:
            continue
        suffix = Path(path).suffix.lower()
        if fleet:
            scannable = path.startswith(".github/workflows/") and suffix in (".yml", ".yaml")
        else:
            scannable = (
                (path.startswith(".github/workflows/") and suffix in (".yml", ".yaml"))
                or suffix == ".json"
                or suffix in (".md", ".py", ".yml", ".yaml", ".toml", ".txt")
            )
        if scannable:
            paths.append(path)
    return paths


def fetch_repository_pin_files(
    repository: str,
    ref: str,
    token: str,
    *,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
    fleet: bool = False,
) -> dict[str, str]:
    patterns = list(exclude_globs)
    if include_default_excludes:
        patterns = list(DEFAULT_EXCLUDE_GLOBS) + list(exclude_globs)
    files: dict[str, str] = {}
    for path in _remote_scannable_paths(repository, ref, token, patterns, fleet=fleet):
        text = _fetch_repository_file(repository, path, ref, token)
        if text is not None:
            files[path] = text
    return files


def _failure_pin_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if report["inconsistent"]:
        for pin in report["pins"]:
            rows.append({**pin, "reason": "inconsistent"})
        return rows
    stale_set = set(report.get("stale_commits") or [])
    for pin in report["pins"]:
        if pin["commit"] in stale_set:
            rows.append({**pin, "reason": "stale"})
    return rows


def audit_fleet_child(
    repository: str,
    *,
    parent_repository: str,
    main_sha: str,
    token: str,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
) -> dict[str, Any] | None:
    revision, _branch = _resolve_default_branch_head(repository, token)
    files = fetch_repository_pin_files(
        repository,
        revision,
        token,
        exclude_globs=exclude_globs,
        include_default_excludes=include_default_excludes,
        fleet=True,
    )
    pins = scan_text_files(files, parent_repository=parent_repository)
    if not pins:
        return None
    report = evaluate(
        pins,
        parent_repository=parent_repository,
        main_sha=main_sha,
        token=token,
        enforce=False,
    )
    if not report["inconsistent"] and not report["stale"]:
        return None
    return {
        "repository": repository,
        "revision": revision,
        "pins": _failure_pin_rows(report),
        "inconsistent": report["inconsistent"],
        "stale": report["stale"],
    }


def run_fleet(
    *,
    owner: str = ORG_OWNER,
    parent_repository: str = PARENT_REPOSITORY,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
    token: str | None = None,
    enforce: bool = False,
    child_heads: str | Path | None = "child-heads.json",
) -> dict[str, Any]:
    if not token:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    heads_path = Path(child_heads) if child_heads else None
    main_sha, branch = fetch_parent_main_sha(parent_repository, None)
    coverage: dict[str, Any] = {}
    children = _list_fleet_child_repositories(
        owner, parent_repository, token, heads_path, coverage=coverage
    )
    failures: list[dict[str, Any]] = []
    audit_errors: list[dict[str, Any]] = []
    fetch_warnings: list[dict[str, Any]] = []
    skipped_repositories = 0
    audited_names: set[str] = set()
    for name in children:
        try:
            item = audit_fleet_child(
                name,
                parent_repository=parent_repository,
                main_sha=main_sha,
                token=token,
                exclude_globs=exclude_globs,
                include_default_excludes=include_default_excludes,
            )
        except ParentPinError as error:
            kind = _classify_fleet_child_error(error)
            if kind == "skip":
                skipped_repositories += 1
                continue
            if kind == "warn":
                fetch_warnings.append({"message": _fleet_auth_warning_message(error)})
                continue
            entry: dict[str, Any] = {"message": str(error)}
            if not _redact_fleet_child_name(error):
                entry["repository"] = name
            audit_errors.append(entry)
            continue
        if item is not None:
            failures.append(item)
        audited_names.add(name)
    # A child may become private between inventory and audit. Do not expose
    # its name through registry diagnostics after it has been excluded.
    if "registry_missing_repositories" in coverage:
        missing = coverage["registry_missing_repositories"]
        coverage["registry_missing_count"] = len(missing)
        coverage["registry_missing_repositories"] = [
            name for name in missing if name in audited_names
        ]
    enforce_fail = bool(failures or audit_errors)
    return {
        "schema": FLEET_SCHEMA,
        "parent_repository": parent_repository,
        "parent_main_sha": main_sha,
        "parent_default_branch": branch,
        "checked_repositories": len(children),
        "audited_repositories": len(audited_names),
        "complete": not (audit_errors or fetch_warnings),
        **coverage,
        "skipped_repositories": skipped_repositories,
        "failure_count": len(failures),
        "failures": failures,
        "audit_error_count": len(audit_errors),
        "audit_errors": audit_errors,
        "fetch_warning_count": len(fetch_warnings),
        "fetch_warnings": fetch_warnings,
        "enforce": enforce,
        "ok": not (enforce and enforce_fail),
    }


def render_fleet_text(report: dict[str, Any]) -> str:
    skipped = int(report.get("skipped_repositories") or 0)
    lines = [
        "parent-pin-fleet: checked "
        f"{report['checked_repositories']} public child repositories; "
        f"{skipped} skipped; "
        f"{report['failure_count']} pin finding(s); "
        f"{report.get('audit_error_count', 0)} audit error(s); "
        f"{report.get('fetch_warning_count', 0)} fetch warning(s)"
    ]
    lines.append(f"parent main ({report['parent_default_branch']}): {report['parent_main_sha']}")
    if report.get("registry_present"):
        lines.append(
            f"registry: {report['registered_repositories']} public child HEAD baseline(s); "
            f"{report['registry_missing_count']} unregistered public child(ren); "
            f"{report['registry_unmatched_count']} unmatched entry/entries (names withheld)"
        )
    if not report.get("complete", True):
        lines.append("WARNING: fleet audit is incomplete; see fetch warnings and audit errors.")
    for item in report["failures"]:
        lines.append(f"FAIL {item['repository']} @ {item['revision']}")
        for pin in item["pins"]:
            line = "" if pin.get("line") is None else f":{pin['line']}"
            lines.append(
                f"  {pin['reason']} {pin['file']}{line} pin={pin['commit']} ({pin['kind']})"
            )
    level = "ERROR" if report.get("enforce") else "WARNING"
    if report["failure_count"]:
        lines.append(
            f"{level}: at least one public child default branch still pins an old parent commit."
        )
    for item in report.get("fetch_warnings", []):
        lines.append(f"WARNING: {item['message']}")
    for item in report.get("audit_errors", []):
        repo = item.get("repository")
        prefix = f"{repo} " if repo else ""
        lines.append(f"{level}: {prefix}{item['message']}")
    return "\n".join(lines) + "\n"


def emit_fleet_annotations(report: dict[str, Any]) -> None:
    main_sha = report["parent_main_sha"]
    level = "error" if report.get("enforce") else "warning"
    if report.get("registry_present") and report.get("registry_missing_repositories"):
        print(
            "::warning::public children missing from child-heads.json (included in fleet audit): "
            + ", ".join(report["registry_missing_repositories"])
        )
    for item in report.get("fetch_warnings", []):
        print(f"::warning::{item['message']}")
    for item in report.get("audit_errors", []):
        repo = item.get("repository")
        prefix = f"{repo} " if repo else ""
        print(f"::{level}::{prefix}{item['message']}")
    for item in report["failures"]:
        repo = item["repository"]
        for pin in item["pins"]:
            line = pin.get("line") or 1
            if pin["reason"] == "stale":
                message = (
                    f"{repo} {pin['file']}:{line} parent pin {pin['commit']} "
                    f"is behind {report['parent_repository']} main {main_sha}"
                )
            else:
                message = (
                    f"{repo} {pin['file']}:{line} parent pin {pin['commit']} "
                    "is not unified with other parent pins in this repository"
                )
            print(f"::{level}::{message}")


def run(
    root: str | Path,
    *,
    parent_repository: str = PARENT_REPOSITORY,
    exclude_globs: Sequence[str] = (),
    include_default_excludes: bool = True,
    token: str | None = None,
    skip_api: bool = False,
    enforce: bool = False,
) -> dict[str, Any]:
    pins = scan(
        root,
        parent_repository=parent_repository,
        exclude_globs=exclude_globs,
        include_default_excludes=include_default_excludes,
    )
    main_sha = None
    if not skip_api:
        main_sha, _branch = fetch_parent_main_sha(parent_repository, None)
    report = evaluate(
        pins,
        parent_repository=parent_repository,
        main_sha=main_sha,
        token=token,
        enforce=enforce,
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
        "--enforce",
        action="store_true",
        help="Exit non-zero on stale or inconsistent pins (overrides event/ref policy)",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Never fail on findings; emit warnings only",
    )
    parser.add_argument(
        "--event",
        default=None,
        help="GitHub event name for default enforce policy (else GITHUB_EVENT_NAME)",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help="GitHub ref for default enforce policy (else GITHUB_REF)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report on stdout")
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Offline mode: skip parent main resolution and stale checks",
    )
    parser.add_argument(
        "--fleet",
        action="store_true",
        help="Audit public child default branches for stale parent pins (parent CI only)",
    )
    parser.add_argument(
        "--owner",
        default=ORG_OWNER,
        help="GitHub account owner for fleet listing (default: myon-bioinformatics)",
    )
    parser.add_argument(
        "--child-heads",
        default="child-heads.json",
        help="Known child HEAD baseline to cross-check against the public fleet (default: child-heads.json)",
    )
    args = parser.parse_args(argv)
    if args.warn_only and args.enforce:
        parser.error("--enforce and --warn-only are mutually exclusive")
    if args.warn_only:
        enforce_mode = False
    elif args.enforce:
        enforce_mode = True
    else:
        enforce_mode = should_enforce(event_name=args.event, ref=args.ref)
    try:
        if args.fleet:
            report = run_fleet(
                owner=args.owner,
                parent_repository=args.parent,
                exclude_globs=args.exclude,
                include_default_excludes=not args.no_default_excludes,
                enforce=enforce_mode,
                child_heads=args.child_heads,
            )
        else:
            report = run(
                args.root,
                parent_repository=args.parent,
                exclude_globs=args.exclude,
                include_default_excludes=not args.no_default_excludes,
                enforce=enforce_mode,
                skip_api=args.skip_api,
            )
    except ParentPinError as error:
        print(f"::error::{error}", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return 2
    if args.fleet:
        emit_fleet_annotations(report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            sys.stdout.write(render_fleet_text(report))
    else:
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
