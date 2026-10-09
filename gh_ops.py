#!/usr/bin/env python3
"""GitHub REST operations for PR/CI work without the ``gh`` CLI (canonical parent tool).

Stdlib only, so it runs under ``python -S``. Read-only by default: every
write operation takes ``write=True`` / ``--write`` and checks its
preconditions first, so a dry run never issues an HTTP write. The token
comes from ``GITHUB_TOKEN`` (fallback ``GH_TOKEN``) and is never printed.

Every operation is a plain function returning a dict with ``ok``; the CLI
is a thin adapter over them::

    python -S gh_ops.py issue-comments OWNER/REPO 24 --save comments.json
    python -S gh_ops.py --json pr-observe OWNER/REPO 24 > before.json
    python -S gh_ops.py pr-diff before.json after.json
    python -S gh_ops.py issue-comment OWNER/REPO 24 --file comment.md --write
    python -S gh_ops.py comments-file saved-tool-result.txt --last 5   # offline
    python -S gh_ops.py pr-merge OWNER/REPO 11 --sha ecfd0ba --min-checks 5 --write
    python -c "from gh_ops import pr_merge; print(pr_merge('OWNER/REPO', 11, sha='ecfd0ba'))"
    python -S gh_ops.py pr-body-set OWNER/REPO 11 --file body.md --write
    python -S gh_ops.py pr-edit OWNER/REPO 11 --base develop --write
    python -S gh_ops.py file-put OWNER/REPO docs/x.md --from x.md --branch docs --message "add x" --write
    python -S gh_ops.py url compare OWNER/REPO main feature   # no network; prints web + api URLs

Exit codes: 0 = OK, 1 = a checked condition was not met, 2 = input or
communication error.
"""
from __future__ import annotations

import argparse
import importlib.util
import base64
import difflib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

def _load_adjacent_gh_identity():
    base = Path(__file__).resolve().parent
    # Parent checkout uses vendor/; installed consumers use the adjacent copy.
    path = base / "gh_identity.py"
    if not path.is_file():
        path = base / "vendor" / "gh_identity.py"
    spec = importlib.util.spec_from_file_location("_canonical_gh_ops_identity", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load vendored gh_identity from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

gh_identity = _load_adjacent_gh_identity()

API_ROOT = "https://api.github.com"
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
MERGE_METHODS = ("merge", "squash", "rebase")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_HTTP_HINTS = {
    401: "authentication failed; check GITHUB_TOKEN / GH_TOKEN",
    403: "forbidden: missing token permission or rate limited -- not evidence that the feature is unconfigured",
    404: "not found, or not visible to this token",
}


class GhOpsError(Exception):
    """Input or communication error (exit code 2)."""


@dataclass
class Response:
    status: int
    data: Any
    headers: dict = field(default_factory=dict)


Transport = Callable[[str, str, Optional[dict], dict], Response]


def _secrets() -> list[str]:
    return [value for value in (os.environ.get("GITHUB_TOKEN"), os.environ.get("GH_TOKEN")) if value]


def scrub(text: str, extra: tuple[str, ...] = ()) -> str:
    """Replace any token value that leaked into ``text`` with ``***``."""
    for secret in (*_secrets(), *extra):
        if secret:
            text = text.replace(secret, "***")
    return text


def urllib_transport(method: str, url: str, body: dict | None, headers: dict, *, timeout: float = 30.0) -> Response:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            status, raw, reply_headers = reply.status, reply.read(), dict(reply.headers)
    except urllib.error.HTTPError as error:
        status, raw, reply_headers = error.code, error.read(), dict(error.headers or {})
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise GhOpsError(f"{method} {url} failed: {type(error).__name__}: {reason}") from None
    try:
        payload: Any = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    return Response(status, payload, {key.lower(): value for key, value in reply_headers.items()})


class Client:
    """Minimal GitHub REST client with an injectable transport (for tests)."""

    def __init__(self, token: str | None = None, transport: Transport | None = None, api_root: str = API_ROOT):
        if token is None:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self._token = token
        self._transport = transport or urllib_transport
        self.api_root = api_root.rstrip("/")
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, body: dict | None = None, *, params: dict | None = None,
                expect: tuple[int, ...] = (200,)) -> Response:
        url = path if path.startswith("http") else self.api_root + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "browser-test-kit-gh-ops",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self.calls.append((method, url))
        response = self._transport(method, url, body, headers)
        if response.status not in expect:
            message = response.data.get("message", "") if isinstance(response.data, dict) else ""
            hint = _HTTP_HINTS.get(response.status, "")
            text = f"{method} {urllib.parse.urlsplit(url).path} -> HTTP {response.status}"
            text += f": {message}" if message else ""
            text += f" [{hint}]" if hint else ""
            raise GhOpsError(scrub(text, (self._token,)))
        return response

    def get(self, path: str, *, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params).data

    def paginate(self, path: str, *, params: dict | None = None, key: str | None = None, max_pages: int = 50) -> list:
        if max_pages < 1:
            raise GhOpsError("pagination requires a positive page limit")
        items: list = []
        seen = set()
        expected = None
        url, query = path, {"per_page": 100, **(params or {})}
        for _ in range(max_pages):
            request_url = url if url.startswith("http") else self.api_root + url
            if query:
                request_url += ("&" if "?" in request_url else "?") + urllib.parse.urlencode(query)
            if request_url in seen:
                raise GhOpsError("pagination cycle; incomplete response")
            seen.add(request_url)
            response = self.request("GET", url, params=query)
            if key and "total_count" in response.data:
                count = response.data["total_count"]
                if type(count) is not int or count < 0 or (expected is not None and count != expected):
                    raise GhOpsError("pagination total_count invalid or changed; incomplete response")
                expected = count
            items.extend(response.data[key] if key else response.data)
            match = _NEXT_LINK_RE.search(response.headers.get("link", ""))
            if not match:
                if expected is not None and len(items) != expected:
                    raise GhOpsError("pagination total_count mismatch; incomplete response")
                return items
            url, query = match.group(1), None
        raise GhOpsError("pagination page limit exceeded; incomplete response")


def _repo(repo: str) -> str:
    if not _REPO_RE.match(repo or ""):
        raise GhOpsError(f"repository must look like OWNER/REPO, got {repo!r}")
    return repo


def _sha(sha: str) -> str:
    if not _SHA_RE.match(sha or ""):
        raise GhOpsError(f"SHA must be 4-40 hex characters, got {sha!r}")
    return sha.lower()


def _quote(segment: str) -> str:
    """Quote one URL path segment, keeping ``/`` (branch names and file paths may contain it)."""
    return urllib.parse.quote(str(segment), safe="/")


def _client(client: Client | None) -> Client:
    return client if client is not None else Client()


def _preview(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: max(limit - 1, 0)] + "…"


def _login(comment: dict) -> str:
    for key in ("user", "author"):
        value = comment.get(key)
        if isinstance(value, dict):
            return value.get("login") or ""
        if isinstance(value, str):
            return value
    return ""


def _digest(comments: list, *, last: int | None, author: str | None, preview: int,
            show: tuple[int, ...]) -> tuple[list, dict]:
    """Rows (index, id, created_at, author, chars, preview, url) plus full bodies for ``show``."""
    rows = []
    for index, comment in enumerate(comments):
        body = comment.get("body") or ""
        rows.append({
            "index": index,
            "id": comment.get("id"),
            "created_at": comment.get("created_at"),
            "author": _login(comment),
            "chars": len(body),
            "preview": _preview(body, preview),
            "url": comment.get("html_url"),
        })
    if author:
        rows = [row for row in rows if row["author"] == author]
    if last:
        rows = rows[-last:]
    shown = {}
    for index in show:
        if not 0 <= index < len(comments):
            raise GhOpsError(f"no comment with index {index} (0..{len(comments) - 1})")
        shown[index] = comments[index].get("body") or ""
    return rows, shown


# --- read operations -----------------------------------------------------------

def comments_file(path: str, *, last: int | None = None, author: str | None = None, preview: int = 110,
                  show: tuple[int, ...] = ()) -> dict:
    """Digest comments already saved to a file (``-`` for stdin), offline: no token, no network.

    Accepts a JSON array of comments (for example a tool result that was too
    large to display and was saved to disk) or an object with ``comments``
    (and optionally ``issue``), as written by ``issue-comments --save``.
    """
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GhOpsError(f"{path}: not JSON ({error})") from None
    issue = data.get("issue") if isinstance(data, dict) and isinstance(data.get("issue"), dict) else {}
    comments = data.get("comments") if isinstance(data, dict) else data
    if not isinstance(comments, list) or not all(isinstance(item, dict) for item in comments):
        raise GhOpsError(f"{path}: expected a JSON array of comments or an object with \"comments\"")
    rows, shown = _digest(comments, last=last, author=author, preview=preview, show=show)
    return {"ok": True, "source": path, "title": issue.get("title"), "state": issue.get("state"),
            "total_comments": len(comments), "comments": rows, "shown": shown}


def issue_comments(repo: str, number: int, *, since: str | None = None, last: int | None = None,
                   author: str | None = None, preview: int = 110, show: tuple[int, ...] = (),
                   save: str | None = None, client: Client | None = None) -> dict:
    """Digest an issue/PR conversation: one line per comment, never the whole dump.

    Rows carry index, created_at, author, character count, and a whitespace-
    collapsed preview. Full bodies are only returned for the indexes in
    ``show``; ``save`` writes the complete issue + comments JSON to a file so
    huge threads can be read selectively instead of flooding a log/context.
    """
    client = _client(client)
    base = f"/repos/{_repo(repo)}/issues/{int(number)}"
    issue = client.get(base)
    comments = client.paginate(base + "/comments", params={"since": since} if since else None)
    rows, shown = _digest(comments, last=last, author=author, preview=preview, show=show)
    saved_to = None
    if save:
        path = Path(save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"issue": issue, "comments": comments}, ensure_ascii=False, indent=2), encoding="utf-8")
        saved_to = str(path)
    return {
        "ok": True,
        "repo": repo,
        "number": int(number),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "is_pull_request": "pull_request" in issue,
        "body_chars": len(issue.get("body") or ""),
        "total_comments": len(comments),
        "comments": rows,
        "shown": shown,
        "saved_to": saved_to,
    }


def pr_status(repo: str, number: int, *, client: Client | None = None) -> dict:
    """One-line PR state: mergeability, head, size, and merged flag."""
    try:
        pr = (client.get(f"/repos/{_repo(repo)}/pulls/{int(number)}") if client is not None
              else gh_identity.request("GET", f"repos/{_repo(repo)}/pulls/{int(number)}"))
    except gh_identity.Error as error:
        raise GhOpsError(error.code) from error
    return {
        "ok": True,
        "number": int(number),
        "state": pr.get("state"),
        "draft": bool(pr.get("draft")),
        "merged": bool(pr.get("merged")),
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "head_ref": (pr.get("head") or {}).get("ref"),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "commits": pr.get("commits"),
        "changed_files": pr.get("changed_files"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "url": pr.get("html_url"),
    }


def compare_pr_head_identity(repo: str, number: int, local: dict, *, client: Client | None = None) -> dict:
    """Compare a caller-supplied local identity with the current PR head via GHI."""
    status = pr_status(repo, number, client=client)
    result = gh_identity.compare_sha(local, status.get("head_sha"))
    return {**result, "repo": repo, "number": int(number), "head_ref": status.get("head_ref"),
            "base_ref": status.get("base_ref")}



def _check_runs(client: Client, repo: str, sha: str) -> list:
    return client.paginate(f"/repos/{repo}/commits/{sha}/check-runs", key="check_runs")


def _summarize_checks(runs: list, min_checks: int) -> dict:
    """Compatibility shape over GHI's canonical pure check classification."""
    summary = gh_identity.summarize_checks(runs, len(runs), min_checks)
    normalized = summary["checks"]
    pending = [run for run in normalized if run.get("status") != "completed"]
    failed = [run for run in normalized
              if run.get("status") == "completed" and
              run.get("conclusion") not in PASSING_CONCLUSIONS]
    succeeded = [run for run in normalized if run.get("conclusion") == "success"]
    if len(normalized) < min_checks:
        reason = f"only {len(normalized)} check run(s), expected at least {min_checks}"
    elif pending:
        reason = f"{len(pending)} check run(s) still pending"
    elif failed:
        reason = f"{len(failed)} check run(s) failed: " + ", ".join(sorted(run.get("name", "?") for run in failed))
    elif not succeeded:
        reason = "no check run concluded success (all skipped/neutral)"
    else:
        reason = ""
    return {
        "ok": summary["state"] == "green",
        "reason": reason,
        "total": summary["count"],
        "pending": len(pending),
        "failed": len(failed),
        "succeeded": len(succeeded),
        "runs": [
            {**run, "head_sha": original.get("head_sha")}
            for run, original in zip(normalized, runs)
        ],
    }


def checks_status(repo: str, sha: str, *, min_checks: int = 1, client: Client | None = None) -> dict:
    """One-shot check summary for a SHA; unlike checks_wait this never polls."""
    if min_checks < 1:
        raise GhOpsError("--min must be at least 1; zero check runs never counts as green")
    repo, sha = _repo(repo), _sha(sha)
    summary = _summarize_checks(_check_runs(_client(client), repo, sha), min_checks)
    if summary["ok"]:
        state = "green"
    elif summary["pending"] or summary["total"] < min_checks:
        state = "pending"
    else:
        state = "failed"
    return {"sha": sha, "state": state, **summary}


def _activity_digest(items: list) -> dict:
    """Stable small digest for conversation/review activity."""
    if not items:
        return {"count": 0, "latest_id": None, "latest_at": None}

    def stamp(item):
        return str(item.get("updated_at") or item.get("submitted_at") or item.get("created_at") or "")

    latest = max(items, key=lambda item: (stamp(item), int(item.get("id") or 0)))
    return {"count": len(items), "latest_id": latest.get("id"), "latest_at": stamp(latest) or None}


def _review_digest(items: list) -> dict:
    """Activity digest plus review-state counts so approve/dismiss changes are observable."""
    result = _activity_digest(items)
    states = {}
    for item in items:
        state = item.get("state")
        if state:
            states[state] = states.get(state, 0) + 1
    result["states"] = {key: states[key] for key in sorted(states)}
    return result


def pr_observe(repo: str, number: int, *, min_checks: int = 1, client: Client | None = None) -> dict:
    """Return a compact PR observation suitable for persisted snapshot comparison.

    A failed or pending check set does not make the observation itself fail.
    Checks are bound to the current PR head. Conversation comments, reviews,
    and inline review comments are digested separately. Semantic labels such
    as Blocking or Should are intentionally not inferred from prose.
    """
    if min_checks < 1:
        raise GhOpsError("--min must be at least 1; zero check runs never counts as green")
    client, repo, number = _client(client), _repo(repo), int(number)
    base = f"/repos/{repo}/pulls/{number}"
    pr = client.get(base)
    head = (pr.get("head") or {}).get("sha")
    if not head:
        raise GhOpsError("PR response is missing head.sha")
    checks = checks_status(repo, head, min_checks=min_checks, client=client)
    issue_comments = client.paginate(f"/repos/{repo}/issues/{number}/comments")
    reviews = client.paginate(base + "/reviews")
    review_comments = client.paginate(base + "/comments")
    final_pr = client.get(base)
    final_head = (final_pr.get("head") or {}).get("sha")
    head_identity = gh_identity.compare_sha({"sha": head}, final_head)
    if not head_identity["same"]:
        return {"ok": False, "schema": "gh-ops-pr-observation/1", "repo": repo, "number": number,
                "stale": True, "observed_head_sha": head, "current_head_sha": final_head,
                "identity": head_identity,
                "reason": "PR head changed during observation; discard this snapshot and retry"}
    return {
        "ok": True,
        "schema": "gh-ops-pr-observation/1",
        "repo": repo,
        "number": number,
        "state": final_pr.get("state"),
        "draft": bool(final_pr.get("draft")),
        "merged": bool(final_pr.get("merged")),
        "mergeable_state": final_pr.get("mergeable_state"),
        "head_sha": head,
        "head_ref": (final_pr.get("head") or {}).get("ref"),
        "base_ref": (final_pr.get("base") or {}).get("ref"),
        "checks": checks,
        "activity": {
            "issue_comments": _activity_digest(issue_comments),
            "reviews": _review_digest(reviews),
            "review_comments": _activity_digest(review_comments),
        },
        "url": final_pr.get("html_url"),
    }


def pr_observation_diff(before: dict, after: dict) -> dict:
    """Compare two pr_observe snapshots without network access."""
    schema = "gh-ops-pr-observation/1"
    if before.get("schema") != schema or after.get("schema") != schema:
        raise GhOpsError(f"both snapshots must use schema {schema!r}")
    if before.get("ok") is not True or after.get("ok") is not True:
        raise GhOpsError("cannot diff an incomplete/stale observation")
    if (before.get("repo"), before.get("number")) != (after.get("repo"), after.get("number")):
        raise GhOpsError("snapshots refer to different pull requests")
    events = []

    def changed(kind, field):
        if before.get(field) != after.get(field):
            events.append({"type": kind, "from": before.get(field), "to": after.get(field)})

    changed("head_changed", "head_sha")
    changed("state_changed", "state")
    changed("draft_changed", "draft")
    if not before.get("merged") and after.get("merged"):
        events.append({"type": "merged", "from": False, "to": True})
    elif before.get("merged") != after.get("merged"):
        events.append({"type": "merged_changed", "from": before.get("merged"), "to": after.get("merged")})
    old_checks = (before.get("checks") or {}).get("state")
    new_checks = (after.get("checks") or {}).get("state")
    if old_checks != new_checks:
        kind = "ci_became_green" if new_checks == "green" else "ci_failed" if new_checks == "failed" else "ci_state_changed"
        events.append({"type": kind, "from": old_checks, "to": new_checks})
    for key in ("issue_comments", "reviews", "review_comments"):
        old = (before.get("activity") or {}).get(key)
        new = (after.get("activity") or {}).get(key)
        if old != new:
            events.append({"type": key + "_changed", "from": old, "to": new})
    return {"ok": True, "schema": "gh-ops-pr-observation-diff/1", "repo": after["repo"],
            "number": after["number"], "changed": bool(events), "events": events}


def pr_observation_diff_files(before_path: str, after_path: str) -> dict:
    """Read two observation JSON files and compare them offline."""
    try:
        before = json.loads(Path(before_path).read_text(encoding="utf-8"))
        after = json.loads(Path(after_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GhOpsError(f"observation file is not JSON: {error}") from None
    return pr_observation_diff(before, after)


def pr_for_branch(repo: str, branch: str, *, state: str = "all", client: Client | None = None) -> dict:
    """PRs whose head is ``branch``: number, state, merged, head SHA, base, and URL.

    ``branch`` may be ``owner:branch`` for a fork; a bare name means the
    repository owner. ``ok`` is False when there is none, so "is there already
    a PR for this branch, and was it merged?" is answered before creating one.
    """
    owner = _repo(repo).split("/")[0]
    head = branch if ":" in branch else f"{owner}:{branch}"
    pulls = _client(client).paginate(f"/repos/{_repo(repo)}/pulls", params={"head": head, "state": state})
    rows = [{
        "number": pr.get("number"),
        "state": pr.get("state"),
        "merged": bool(pr.get("merged_at")),
        "draft": bool(pr.get("draft")),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "base": (pr.get("base") or {}).get("ref"),
        "title": pr.get("title"),
        "url": pr.get("html_url"),
    } for pr in pulls]
    return {"ok": bool(rows), "repo": repo, "head": head, "state": state, "pulls": rows}


def open_prs(owner: str, *, org: bool = False, repos: tuple[str, ...] = (), limit: int = 50,
             client: Client | None = None) -> dict:
    """Open PRs across every repository of ``owner`` in one search call, most recently updated first.

    ``org=True`` searches ``org:OWNER`` instead of ``user:OWNER``. With
    ``repos``, each named repository is read through its own
    ``/repos/OWNER/NAME/pulls`` endpoint instead, for tokens or proxies
    that allow repository-scoped calls but not search. ``ok`` is False when
    nothing is open.
    """
    if client is None and not repos:
        qualifier = "org" if org else "user"
        try:
            result = gh_identity.search(f"is:open archived:false {qualifier}:{owner}", kind="pr", max_items=limit)
        except gh_identity.Error as error:
            raise GhOpsError(error.code) from error
        rows = [{"repo": row["repository"], "number": row["number"], "draft": bool(row.get("draft")),
                 "updated_at": row.get("updated_at"), "author": row.get("author") or "",
                 "title": row["title"], "url": row["url"]} for row in result["items"]]
        return {"ok": bool(rows), "owner": owner, "total": result["total_count"], "pulls": rows,
                "complete": result["complete"], "truncated": result["truncated"]}
    client = _client(client)
    if repos:
        rows = []
        for name in repos:
            for pr in client.paginate(f"/repos/{_repo(owner + '/' + name)}/pulls", params={"state": "open"}):
                rows.append({"repo": f"{owner}/{name}", "number": pr.get("number"), "draft": bool(pr.get("draft")),
                             "updated_at": pr.get("updated_at"), "author": (pr.get("user") or {}).get("login", ""),
                             "title": pr.get("title"), "url": pr.get("html_url")})
        rows.sort(key=lambda row: str(row["updated_at"]), reverse=True)
        return {"ok": bool(rows), "owner": owner, "total": len(rows), "pulls": rows[:limit]}
    qualifier = "org" if org else "user"
    data = client.get("/search/issues", params={
        "q": f"is:pr is:open archived:false {qualifier}:{owner}", "sort": "updated", "order": "desc",
        "per_page": max(1, min(int(limit), 100))}) or {}
    rows = [{
        "repo": str(item.get("repository_url", "")).rsplit("/repos/", 1)[-1],
        "number": item.get("number"),
        "draft": bool(item.get("draft")),
        "updated_at": item.get("updated_at"),
        "author": (item.get("user") or {}).get("login", ""),
        "title": item.get("title"),
        "url": item.get("html_url"),
    } for item in data.get("items", [])[:limit]]
    return {"ok": bool(rows), "owner": owner, "total": data.get("total_count", len(rows)), "pulls": rows}


def _all_items(client: Client, path: str, *, params: dict | None = None,
               empty_repository: bool = False):
    """Stream every REST page, failing rather than returning truncated counts."""
    query = {"per_page": 100, **(params or {})}
    seen = set()
    while True:
        response = client.request("GET", path, params=query,
                                  expect=(200, 409) if empty_repository else (200,))
        if response.status == 409:
            # GitHub may include documentation_url/status alongside the message.
            if not seen and isinstance(response.data, dict) and response.data.get("message") == "Git Repository is empty.":
                return
            raise GhOpsError(f"GET {path} -> HTTP 409: cannot count commits")
        if not isinstance(response.data, list):
            raise GhOpsError(f"GET {path}: expected a REST list")
        yield from response.data
        match = _NEXT_LINK_RE.search(response.headers.get("link", ""))
        if not match:
            return
        path, query = match.group(1), None
        target, root = urllib.parse.urlsplit(path), urllib.parse.urlsplit(client.api_root)
        if (target.scheme, target.netloc) != (root.scheme, root.netloc):
            raise GhOpsError("pagination link points outside the API host")
        if path in seen:
            raise GhOpsError("repeated pagination link; counts would be incomplete")
        seen.add(path)


def _repositories(owner: str, org: bool, repos: tuple[str, ...], client: Client) -> list[str]:
    _repo(owner + "/placeholder")
    if repos:
        return sorted({_repo(owner + "/" + name) for name in repos})
    kind = "orgs" if org else "users"
    return sorted({_repo(item["full_name"]) for item in
                   _all_items(client, f"/{kind}/{owner}/repos", params={"type": "all"})})


def open_issues(owner: str, *, org: bool = False, repos: tuple[str, ...] = (),
                client: Client | None = None) -> dict:
    """List all open Issues across selected repositories, excluding PRs."""
    client = _client(client)
    rows = []
    for repo in _repositories(owner, org, repos, client):
        for issue in _all_items(client, f"/repos/{repo}/issues", params={"state": "open"}):
            if "pull_request" not in issue:
                rows.append({"repo": repo, "number": issue.get("number"), "title": issue.get("title"),
                             "author": _login(issue), "updated_at": issue.get("updated_at"),
                             "url": issue.get("html_url")})
    return {"ok": True, "owner": owner, "total": len(rows), "issues": rows}


def repo_counts(owner: str, *, org: bool = False, repos: tuple[str, ...] = (),
                client: Client | None = None) -> dict:
    """Count open Issues/PRs and default-branch reachable commits using GET only.

    Includes zero-count and archived repositories. Counts reflect sequential
    REST reads, not an atomic snapshot. Errors propagate; unavailable != zero.
    """
    client = _client(client)
    rows = []
    for repo in _repositories(owner, org, repos, client):
        issues = pulls = 0
        for item in _all_items(client, f"/repos/{repo}/issues", params={"state": "open"}):
            if "pull_request" in item:
                pulls += 1
            else:
                issues += 1
        metadata = client.get(f"/repos/{repo}")
        branch = metadata.get("default_branch")
        if not branch:
            raise GhOpsError(f"{repo}: missing default_branch; cannot count commits")
        commits = sum(1 for _ in _all_items(client, f"/repos/{repo}/commits",
                                           params={"sha": branch}, empty_repository=True))
        rows.append({"repo": repo, "open_issues": issues, "open_prs": pulls,
                     "commits": commits, "default_branch": branch})
    totals = {key: sum(row[key] for row in rows) for key in ("open_issues", "open_prs", "commits")}
    return {"ok": True, "owner": owner, "org": org, "repositories": rows,
            "repository_count": len(rows), "totals": totals, "commit_scope": "default_branch"}


def checks_wait(repo: str, sha: str, *, min_checks: int = 1, timeout: float = 600.0, interval: float = 15.0,
                client: Client | None = None, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> dict:
    """Wait until at least ``min_checks`` check runs exist and all completed.

    Zero check runs never count as green: timing out with fewer than
    ``min_checks`` runs, or with runs still pending, returns ``ok: False``.
    Annotations (title/message) of runs that have any are attached.
    """
    if min_checks < 1:
        raise GhOpsError("--min must be at least 1; zero check runs never counts as green")
    client, repo, sha = _client(client), _repo(repo), _sha(sha)
    deadline = clock() + timeout
    while True:
        runs = _check_runs(client, repo, sha)
        summary = _summarize_checks(runs, min_checks)
        if summary["total"] >= min_checks and not summary["pending"]:
            break
        if clock() >= deadline:
            summary.update(ok=False, reason="timeout: " + summary["reason"])
            return {"sha": sha, "timed_out": True, **summary}
        sleep(interval)
    for run in summary["runs"]:
        if run["annotations_count"]:
            annotations = client.paginate(f"/repos/{repo}/check-runs/{run['id']}/annotations")
            run["annotations"] = [
                {"level": item.get("annotation_level"), "title": item.get("title"), "message": item.get("message")}
                for item in annotations
            ]
    return {"sha": sha, "timed_out": False, **summary}


def runs(repo: str, *, sha: str | None = None, workflow: str | None = None, limit: int = 30,
         client: Client | None = None) -> dict:
    """List workflow runs for a head SHA or for one workflow file."""
    if (sha is None) == (workflow is None):
        raise GhOpsError("pass exactly one of --sha or --workflow")
    repo = _repo(repo)
    if workflow is not None:
        path = f"/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/runs"
        params = {"per_page": limit}
    else:
        path, params = f"/repos/{repo}/actions/runs", {"head_sha": _sha(sha or ""), "per_page": limit}
    data = _client(client).get(path, params=params)
    rows = [
        {
            "id": run.get("id"),
            "name": run.get("name"),
            "event": run.get("event"),
            "head_sha": run.get("head_sha"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
        }
        for run in (data or {}).get("workflow_runs", [])
    ]
    return {"ok": True, "total_count": (data or {}).get("total_count", len(rows)), "runs": rows}


def workflow_state(repo: str, workflow: str, *, client: Client | None = None) -> dict:
    """Report a workflow's state; ``ok`` only when it is ``active``."""
    path = f"/repos/{_repo(repo)}/actions/workflows/{urllib.parse.quote(workflow, safe='')}"
    data = _client(client).get(path)
    return {"ok": data.get("state") == "active", "workflow": workflow, "state": data.get("state"),
            "id": data.get("id"), "path": data.get("path")}


# --- write operations (dry run unless write=True) ---------------------------------

def issue_comment_create(repo: str, number: int, *, body: str, write: bool = False,
                         client: Client | None = None) -> dict:
    """Create one top-level PR conversation comment, dry-run by default.

    The PR endpoint is read first so an Issue number cannot be mistaken for a
    PR. After a successful POST the returned comment id is re-read and the body
    is verified. If post-write verification fails, posted remains true and the
    result tells callers to inspect the conversation before any retry.
    """
    if not body.strip():
        raise GhOpsError("comment body must not be empty")
    client, repo, number = _client(client), _repo(repo), int(number)
    pr = client.get(f"/repos/{repo}/pulls/{number}")
    path = f"/repos/{repo}/issues/{number}/comments"
    if not write:
        return {"ok": True, "dry_run": True, "posted": False, "number": number,
                "head_sha": (pr.get("head") or {}).get("sha"), "body_chars": len(body),
                "would_post": {"path": path, "body": {"body": body}}}
    response = client.request("POST", path, {"body": body}, expect=(201,))
    data = response.data if isinstance(response.data, dict) else {}
    comment_id = data.get("id")
    if comment_id is None:
        return {"ok": False, "dry_run": False, "posted": True, "number": number, "comment_id": None,
                "verified": False,
                "reason": "comment POST returned success without an id; inspect the PR conversation before retrying"}
    try:
        observed = client.get(f"/repos/{repo}/issues/comments/{int(comment_id)}")
    except GhOpsError as error:
        return {"ok": False, "dry_run": False, "posted": True, "number": number, "comment_id": comment_id,
                "verified": False,
                "reason": f"comment was posted but verification failed; inspect before retrying: {error}"}
    verified = (observed.get("body") or "") == body
    return {"ok": verified, "dry_run": False, "posted": True, "number": number, "comment_id": comment_id,
            "url": observed.get("html_url") or data.get("html_url"), "verified": verified,
            "reason": "" if verified else "comment was posted but body verification differed; inspect before retrying"}


def workflow_dispatch(repo: str, workflow: str, *, ref: str, inputs: dict | None = None, write: bool = False,
                      client: Client | None = None) -> dict:
    """Trigger ``workflow_dispatch`` after confirming the workflow is active."""
    client = _client(client)
    state = workflow_state(repo, workflow, client=client)
    if not state["ok"]:
        return {"ok": False, "dry_run": not write, "dispatched": False,
                "reason": f"workflow state is {state['state']!r}, not 'active'"}
    body: dict = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    path = f"/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/dispatches"
    if not write:
        return {"ok": True, "dry_run": True, "dispatched": False, "would_post": {"path": path, "body": body}}
    client.request("POST", path, body, expect=(204,))
    return {"ok": True, "dry_run": False, "dispatched": True}


def pr_body_replace(repo: str, number: int, *, old: str, new: str, write: bool = False,
                    client: Client | None = None) -> dict:
    """Replace one anchor in a PR body, only when it occurs exactly once."""
    if not old:
        raise GhOpsError("--old must not be empty")
    client = _client(client)
    path = f"/repos/{_repo(repo)}/pulls/{int(number)}"
    body = client.get(path).get("body") or ""
    count = body.count(old)
    if count != 1:
        reason = f"anchor must occur exactly once in the PR body, found {count}"
        if count == 0 and "\r\n" in body and "\r\n" not in old:
            reason += " (the body uses CRLF line endings)"
        return {"ok": False, "dry_run": not write, "updated": False, "anchor_count": count, "reason": reason}
    updated = body.replace(old, new, 1)
    if not write:
        return {"ok": True, "dry_run": True, "updated": False, "anchor_count": 1, "new_length": len(updated)}
    client.request("PATCH", path, {"body": updated})
    verified = new in (client.get(path).get("body") or "")
    return {"ok": verified, "dry_run": False, "updated": True, "anchor_count": 1, "verified": verified,
            "reason": "" if verified else "PR body does not contain the new text after the update"}


def _lines_differ(old: str, new: str) -> int:
    """Count how many lines differ between ``old`` and ``new`` (a changed line counts once)."""
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines())
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal")


def pr_body_set(repo: str, number: int, *, new: str, write: bool = False, client: Client | None = None) -> dict:
    """Replace a PR's entire body with ``new`` (whole-file overwrite).

    Unlike :func:`pr_body_replace`, this does not require an anchor: it
    always overwrites the full body. A dry run reports the old/new size in
    characters and how many lines differ; when the text is already
    identical, this returns ``ok: True`` with no HTTP write at all
    (``reason: "unchanged"``). With ``write=True``, it PATCHes the body and
    re-reads the PR to verify the body now equals ``new`` exactly.
    """
    client = _client(client)
    path = f"/repos/{_repo(repo)}/pulls/{int(number)}"
    old = client.get(path).get("body") or ""
    if old == new:
        return {"ok": True, "changed": False, "old_chars": len(old), "new_chars": len(new),
                "lines_differ": 0, "reason": "unchanged"}
    lines_differ = _lines_differ(old, new)
    if not write:
        return {"ok": True, "dry_run": True, "changed": False, "old_chars": len(old), "new_chars": len(new),
                "lines_differ": lines_differ}
    client.request("PATCH", path, {"body": new})
    verified = (client.get(path).get("body") or "") == new
    return {"ok": verified, "dry_run": False, "changed": True, "old_chars": len(old), "new_chars": len(new),
            "lines_differ": lines_differ, "verified": verified,
            "reason": "" if verified else "PR body does not match the file after the update"}


_PR_EDIT_STATES = ("open", "closed")


def pr_edit(repo: str, number: int, *, title: str | None = None, base: str | None = None,
            state: str | None = None, write: bool = False, client: Client | None = None) -> dict:
    """REST PATCH of a PR's ``title``, ``base`` branch, and/or open/closed ``state``.

    At least one of ``title``, ``base``, ``state`` is required (else
    ``GhOpsError``, CLI exit 2). Changing draft <-> ready for review is a
    GraphQL-only operation (``markPullRequestReadyForReview`` /
    ``convertPullRequestToDraft``) and is out of scope here. A dry run
    reports current -> new for each requested field; with ``write=True``,
    it PATCHes only those fields, then re-reads the PR to verify.
    """
    fields = {key: value for key, value in (("title", title), ("base", base), ("state", state)) if value is not None}
    if not fields:
        raise GhOpsError("pass at least one of --title, --base, --state")
    if state is not None and state not in _PR_EDIT_STATES:
        raise GhOpsError(f"--state must be one of {', '.join(_PR_EDIT_STATES)}")
    client = _client(client)
    path = f"/repos/{_repo(repo)}/pulls/{int(number)}"
    pr = client.get(path)
    current = {"title": pr.get("title"), "base": (pr.get("base") or {}).get("ref"), "state": pr.get("state")}
    report = {key: {"from": current[key], "to": value} for key, value in fields.items()}
    if not write:
        return {"ok": True, "dry_run": True, "changed": False, "fields": report}
    client.request("PATCH", path, fields)
    updated = client.get(path)
    now = {"title": updated.get("title"), "base": (updated.get("base") or {}).get("ref"), "state": updated.get("state")}
    verified = all(now[key] == value for key, value in fields.items())
    return {"ok": verified, "dry_run": False, "changed": True, "fields": report, "verified": verified,
            "reason": "" if verified else "PR fields do not match the requested values after the update"}


def pr_merge(repo: str, number: int, *, sha: str, min_checks: int = 1, method: str = "squash",
             message: str | None = None, title: str | None = None, write: bool = False,
             client: Client | None = None) -> dict:
    """Verify, then (only with ``write=True``) merge a PR in one call.

    Preconditions: open, ``mergeable_state == "clean"``, head matches
    ``sha`` (an abbreviated SHA must match exactly one PR commit), and the
    head has at least ``min_checks`` check runs, all completed and passing.
    Any failed precondition returns ``ok: False`` without an HTTP write.
    The merge request always pins ``sha`` to the verified head.
    """
    if method not in MERGE_METHODS:
        raise GhOpsError(f"--method must be one of {', '.join(MERGE_METHODS)}")
    if min_checks < 1:
        raise GhOpsError("--min-checks must be at least 1; zero check runs never counts as green")
    client, repo, prefix = _client(client), _repo(repo), _sha(sha)
    path = f"/repos/{repo}/pulls/{int(number)}"
    pr = client.get(path)
    head = ((pr.get("head") or {}).get("sha") or "").lower()
    matching = [commit["sha"] for commit in client.paginate(path + "/commits") if commit["sha"].lower().startswith(prefix)]
    pre_merge = {
        "state": pr.get("state"),
        "merged": bool(pr.get("merged")),
        "mergeable_state": pr.get("mergeable_state"),
        "head_sha": head,
        "sha_matches_head": head.startswith(prefix),
        "sha_prefix_matches": len(matching),
    }
    failures = []
    if pr.get("state") != "open" or pre_merge["merged"]:
        failures.append("PR is not open")
    if pr.get("mergeable_state") != "clean":
        failures.append(f"mergeable_state is {pr.get('mergeable_state')!r}, not 'clean'")
    if not pre_merge["sha_matches_head"]:
        failures.append(f"head {head[:12]} does not match --sha {sha}")
    elif len(matching) > 1:
        failures.append(f"--sha {sha} matches {len(matching)} commits in this PR; pass a longer SHA")
    elif not matching:
        failures.append("the PR commit list does not contain the head commit; refusing to merge")
    if pre_merge["sha_matches_head"] and len(matching) == 1:
        checks = _summarize_checks(_check_runs(client, repo, head), min_checks)
        if not checks["ok"]:
            failures.append(checks["reason"])
    else:
        checks = {"ok": False, "reason": "not evaluated: --sha does not identify the PR head", "runs": []}
    result = {"ok": False, "dry_run": not write, "pre_merge": pre_merge, "checks": checks,
              "merged": False, "merge_sha": None, "failures": failures}
    if failures:
        return result
    body: dict = {"sha": head, "merge_method": method}
    if title is not None:
        body["commit_title"] = title
    if message is not None:
        body["commit_message"] = message
    if not write:
        result.update(ok=True, would_merge=body)
        return result
    response = client.request("PUT", path + "/merge", body, expect=(200, 405, 409))
    data = response.data if isinstance(response.data, dict) else {}
    merged = response.status == 200 and bool(data.get("merged"))
    result.update(ok=merged, merged=merged, merge_sha=data.get("sha") if merged else None)
    if not merged:
        result["failures"] = [f"merge rejected: HTTP {response.status} {data.get('message', '')}".strip()]
    return result


def file_put(repo: str, path: str, *, content: bytes, branch: str, message: str, write: bool = False,
             allow_default_branch: bool = False, client: Client | None = None) -> dict:
    """Create or update one file through the contents API.

    Refuses to write to the repository's default branch unless
    ``allow_default_branch=True`` (checked first, before anything else, and
    regardless of ``write``). A dry run reports create-vs-update, the local
    size, and the current blob SHA; identical content short-circuits to
    ``ok: True`` with no HTTP write at all (``reason: "unchanged"``). With
    ``write=True``, it PUTs the base64-encoded content and returns the new
    commit SHA.
    """
    client, repo = _client(client), _repo(repo)
    default_branch = client.get(f"/repos/{repo}")["default_branch"]
    if branch == default_branch and not allow_default_branch:
        return {"ok": False, "dry_run": not write, "reason":
                f"refusing to write to the default branch {default_branch!r} without --allow-default-branch"}
    contents_path = f"/repos/{repo}/contents/{_quote(path)}"
    response = client.request("GET", contents_path, params={"ref": branch}, expect=(200, 404))
    current = response.data if response.status == 200 and isinstance(response.data, dict) else {}
    sha = current.get("sha")
    exists = sha is not None
    action = "update" if exists else "create"
    existing = base64.b64decode(current["content"]) if current.get("content") is not None else None
    if exists and existing == content:
        return {"ok": True, "changed": False, "action": action, "path": path, "branch": branch,
                "local_size": len(content), "sha": sha, "reason": "unchanged"}
    if not write:
        return {"ok": True, "dry_run": True, "changed": False, "action": action, "path": path, "branch": branch,
                "local_size": len(content), "sha": sha}
    body: dict = {"message": message, "content": base64.b64encode(content).decode("ascii"), "branch": branch}
    if sha:
        body["sha"] = sha
    data = client.request("PUT", contents_path, body, expect=(200, 201)).data
    commit_sha = (data.get("commit") or {}).get("sha")
    return {"ok": bool(commit_sha), "dry_run": False, "changed": True, "action": action, "path": path,
            "branch": branch, "local_size": len(content), "sha": (data.get("content") or {}).get("sha"),
            "commit_sha": commit_sha}


def sync_main(*, base: str = "main", remote: str = "origin", write: bool = False, push: bool = False,
              cwd: str = ".", run: Callable[..., Any] = subprocess.run) -> dict:
    """Merge ``remote/base`` into the checked-out PR branch (git, no REST).

    Refuses when the tree is dirty or local HEAD differs from the remote
    branch; aborts the merge on conflict. ``push`` pushes only after a clean
    merge and only with ``write``.
    """
    def git(*args: str, check: bool = True) -> Any:
        completed = run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if check and completed.returncode != 0:
            raise GhOpsError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch in {"HEAD", base}:
        raise GhOpsError(f"check out the PR branch first (current: {branch})")
    if git("status", "--porcelain").stdout.strip():
        return {"ok": False, "branch": branch, "reason": "working tree has uncommitted changes"}
    git("fetch", remote, branch, base)
    local = git("rev-parse", "HEAD").stdout.strip()
    remote_head = git("rev-parse", f"{remote}/{branch}").stdout.strip()
    if local != remote_head:
        return {"ok": False, "branch": branch, "reason": f"local HEAD {local[:12]} differs from {remote}/{branch} {remote_head[:12]}"}
    behind = int(git("rev-list", "--count", f"HEAD..{remote}/{base}").stdout.strip() or 0)
    if behind == 0:
        return {"ok": True, "branch": branch, "behind": 0, "merged": False, "reason": "already up to date"}
    if not write:
        return {"ok": True, "branch": branch, "behind": behind, "merged": False, "dry_run": True}
    merge = git("merge", "--no-edit", f"{remote}/{base}", check=False)
    if merge.returncode != 0:
        conflicts = git("diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        git("merge", "--abort", check=False)
        return {"ok": False, "branch": branch, "behind": behind, "merged": False, "conflicts": conflicts,
                "reason": "merge conflict; merge aborted"}
    new_head = git("rev-parse", "HEAD").stdout.strip()
    pushed = False
    if push:
        git("push", remote, f"HEAD:{branch}")
        pushed = True
    return {"ok": True, "branch": branch, "behind": behind, "merged": True, "head": new_head, "pushed": pushed}


# --- URL building (pure string building; no network) -----------------------------

_SEARCH_ENDPOINTS = {
    "code": "code", "issues": "issues", "pullrequests": "issues",
    "repositories": "repositories", "commits": "commits",
}


def url_pr(repo: str, number: int, *, tab: str | None = None) -> dict:
    """Web URL for a PR, or one of its ``checks``/``files``/``commits`` tabs.

    ``files`` and ``commits`` also have a matching REST API URL; ``checks``
    does not (check runs are looked up by commit SHA, not PR number).
    """
    if tab is not None and tab not in ("checks", "files", "commits"):
        raise GhOpsError("--tab must be one of checks, files, commits")
    repo, number = _repo(repo), int(number)
    web = f"https://github.com/{repo}/pull/{number}"
    api: str | None = f"{API_ROOT}/repos/{repo}/pulls/{number}"
    if tab == "checks":
        web, api = web + "/checks", None
    elif tab in ("files", "commits"):
        web, api = f"{web}/{tab}", f"{api}/{tab}"
    return {"ok": True, "web": web, "api": api}


def url_compare(repo: str, base: str, head: str) -> dict:
    """Web and REST API URL comparing ``base...head``."""
    repo = _repo(repo)
    spec = f"{_quote(base)}...{_quote(head)}"
    return {"ok": True, "web": f"https://github.com/{repo}/compare/{spec}",
            "api": f"{API_ROOT}/repos/{repo}/compare/{spec}"}


def url_blame(repo: str, ref: str, path: str, *, line: int | None = None) -> dict:
    """Web blame URL for ``path`` at ``ref``; GitHub has no REST blame endpoint."""
    web = f"https://github.com/{_repo(repo)}/blame/{_quote(ref)}/{_quote(path)}"
    if line is not None:
        web += f"#L{int(line)}"
    return {"ok": True, "web": web, "api": None}


def url_history(repo: str, ref: str, path: str) -> dict:
    """Web commit-history URL for ``path`` at ``ref``, and the equivalent REST commits query."""
    repo = _repo(repo)
    web = f"https://github.com/{repo}/commits/{_quote(ref)}/{_quote(path)}"
    api = f"{API_ROOT}/repos/{repo}/commits?" + urllib.parse.urlencode({"sha": ref, "path": path})
    return {"ok": True, "web": web, "api": api}


def url_runs(repo: str, *, workflow: str | None = None, branch: str | None = None,
             event: str | None = None, status: str | None = None) -> dict:
    """Web and REST API URL listing workflow runs, optionally filtered.

    ``branch``/``event``/``status`` map to the REST list-runs query
    parameters of the same names; the web URL folds them into GitHub's
    ``branch:``/``event:``/``is:`` search syntax instead.
    """
    repo = _repo(repo)
    web, api = f"https://github.com/{repo}/actions", f"{API_ROOT}/repos/{repo}/actions/runs"
    if workflow is not None:
        quoted = _quote(workflow)
        web += f"/workflows/{quoted}"
        api = f"{API_ROOT}/repos/{repo}/actions/workflows/{quoted}/runs"
    filters = " ".join(part for part in (
        f"branch:{branch}" if branch else "", f"event:{event}" if event else "",
        f"is:{status}" if status else "") if part)
    if filters:
        web += "?" + urllib.parse.urlencode({"query": filters})
    query = {key: value for key, value in (("branch", branch), ("event", event), ("status", status)) if value}
    if query:
        api += "?" + urllib.parse.urlencode(query)
    return {"ok": True, "web": web, "api": api}


def url_search(query: str, *, kind: str = "code") -> dict:
    """Web and REST API search URL. ``issues``/``pullrequests`` add an ``is:`` qualifier to ``query``."""
    if kind not in _SEARCH_ENDPOINTS:
        raise GhOpsError(f"--type must be one of {', '.join(_SEARCH_ENDPOINTS)}")
    endpoint = _SEARCH_ENDPOINTS[kind]
    if kind == "pullrequests":
        query = f"is:pr {query}"
    elif kind == "issues":
        query = f"is:issue {query}"
    web = "https://github.com/search?" + urllib.parse.urlencode({"q": query, "type": endpoint})
    api = f"{API_ROOT}/search/{endpoint}?" + urllib.parse.urlencode({"q": query})
    return {"ok": True, "web": web, "api": api}


def url_raw(repo: str, ref: str, path: str) -> dict:
    """Direct raw-content URL for ``path`` at ``ref``, and the equivalent REST contents URL."""
    repo = _repo(repo)
    web = f"https://raw.githubusercontent.com/{repo}/{_quote(ref)}/{_quote(path)}"
    api = f"{API_ROOT}/repos/{repo}/contents/{_quote(path)}?" + urllib.parse.urlencode({"ref": ref})
    return {"ok": True, "web": web, "api": api}


# --- CLI adapter ----------------------------------------------------------------

def _render(command: str, result: dict) -> str:
    if command == "repo-counts":
        return "\n".join(["repo  open issues  open PRs  commits"] + [
            f"{row['repo']}  {row['open_issues']}  {row['open_prs']}  {row['commits']}"
            for row in result["repositories"]])
    if command == "open-issues":
        return "\n".join([f"{result['total']} open Issue(s) for {result['owner']}"] + [
            f"{row['repo']}#{row['number']} {row['title']} {row['url']}" for row in result["issues"]])
    if command in {"issue-comments", "comments-file"}:
        if command == "comments-file":
            title = f" \"{result['title']}\"" if result["title"] else ""
            lines = [f"{result['source']}{title} -- {result['total_comments']} comment(s)"]
        else:
            kind = "PR" if result["is_pull_request"] else "issue"
            lines = [f"{kind} #{result['number']} {result['state']} \"{result['title']}\" -- "
                     f"{result['total_comments']} comment(s), body {result['body_chars']} chars"]
        lines += [f"[{row['index']}] {row['created_at']} {row['author']} {row['chars']} chars: {row['preview']}"
                  for row in result["comments"]]
        for index, body in result["shown"].items():
            lines += [f"--- [{index}] ---", body]
        if result.get("saved_to"):
            lines.append(f"full JSON saved to {result['saved_to']}")
        return "\n".join(lines)
    if command == "open-prs":
        lines = [f"{result['total']} open PR(s) for {result['owner']}"
                 + (f" (showing {len(result['pulls'])})" if result["total"] > len(result["pulls"]) else "")]
        lines += [f"{pr['repo']}#{pr['number']}{' draft' if pr['draft'] else ''} {str(pr['updated_at'])[:10]} "
                  f"{pr['author']} \"{pr['title']}\" {pr['url']}" for pr in result["pulls"]]
        return "\n".join(lines)
    if command == "pr-for-branch":
        if not result["pulls"]:
            scope = "" if result["state"] == "all" else f"{result['state']} "
            return f"no {scope}PR with head {result['head']}"
        return "\n".join(
            f"#{pr['number']} {'merged' if pr['merged'] else pr['state']}{' draft' if pr['draft'] else ''} "
            f"head={str(pr['head_sha'])[:12]} -> {pr['base']} \"{pr['title']}\" {pr['url']}"
            for pr in result["pulls"])
    if command == "pr-status":
        r = result
        return (f"#{r['number']} {r['state']} draft={r['draft']} merged={r['merged']} mergeable={r['mergeable']} "
                f"({r['mergeable_state']}) head={str(r['head_sha'])[:12]} {r['head_ref']} -> {r['base_ref']} "
                f"commits={r['commits']} files={r['changed_files']} +{r['additions']}/-{r['deletions']}")
    if command == "pr-observe":
        r = result
        if not r["ok"]:
            return f"NG pr-observe: {r.get('reason', 'incomplete observation')}"
        checks = r["checks"]
        return (f"#{r['number']} {r['state']} draft={r['draft']} merged={r['merged']} "
                f"head={str(r['head_sha'])[:12]} checks={checks['state']} "
                f"({checks['succeeded']} success/{checks['failed']} failed/{checks['pending']} pending, "
                f"{checks['total']} total) comments={r['activity']['issue_comments']['count']} "
                f"reviews={r['activity']['reviews']['count']} inline={r['activity']['review_comments']['count']}")
    if command == "pr-diff":
        if not result["events"]:
            return f"#{result['number']} no meaningful snapshot changes"
        return "\n".join([f"#{result['number']} {len(result['events'])} change(s)"] + [
            f"  {event['type']}: {event.get('from')!r} -> {event.get('to')!r}" for event in result["events"]])
    if command == "checks-wait":
        lines = [f"{'OK' if result['ok'] else 'NG'} {result['sha'][:12]}: {result['succeeded']} success, "
                 f"{result['failed']} failed, {result['pending']} pending of {result['total']}"
                 + (f" -- {result['reason']}" if result["reason"] else "")]
        for run in result["runs"]:
            lines.append(f"  {run['name']}: {run['conclusion'] or run['status']} (head {str(run['head_sha'])[:12]})")
            for note in run.get("annotations", []):
                lines.append(f"    [{note['level']}] {note['title'] or ''}: {note['message']}")
        return "\n".join(lines)
    if command == "runs":
        return "\n".join([f"{result['total_count']} run(s)"] + [
            f"  {run['id']} {run['event']} {str(run['head_sha'])[:12]} {run['conclusion'] or run['status']} {run['url']}"
            for run in result["runs"]])
    if command == "workflow-state":
        return f"{result['workflow']}: {result['state']}"
    if command == "url":
        lines = [result["web"]]
        if result.get("api"):
            lines.append(f"api: {result['api']}")
        return "\n".join(lines)
    reason = result.get("reason") or "; ".join(result.get("failures", []))
    status = "OK" if result["ok"] else "NG"
    mode = "dry run" if result.get("dry_run") else "applied"
    extra = f" merge_sha={result['merge_sha']}" if result.get("merge_sha") else ""
    return f"{status} {command} ({mode}){extra}" + (f": {reason}" if reason else "")


def _read_text(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gh_ops.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("issue-comments", help="digest an issue/PR conversation (index, time, author, size, preview)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--since", help="ISO 8601; only comments updated at or after this time")
    p.add_argument("--last", type=int, help="keep only the last N rows")
    p.add_argument("--author", help="keep only comments by this login")
    p.add_argument("--preview", type=int, default=110, help="preview length in characters (default 110)")
    p.add_argument("--show", default="", help="comma-separated comment indexes to print in full")
    p.add_argument("--save", help="write the complete issue + comments JSON to this file")

    p = sub.add_parser("comments-file", help="the same digest for comments already saved to a JSON file (offline)")
    p.add_argument("path", help="JSON array of comments, or {\"comments\": [...]} from --save; - for stdin")
    p.add_argument("--last", type=int, help="keep only the last N rows")
    p.add_argument("--author", help="keep only comments by this login")
    p.add_argument("--preview", type=int, default=110, help="preview length in characters (default 110)")
    p.add_argument("--show", default="", help="comma-separated comment indexes to print in full")

    p = sub.add_parser("open-prs", help="open PRs across all repositories of OWNER (none -> exit 1)")
    p.add_argument("owner"); p.add_argument("--org", action="store_true", help="OWNER is an organization")
    p.add_argument("--limit", type=int, default=50, help="rows to show (max 100)")
    p.add_argument("--repos", default="", help="comma-separated repository names: read each via /repos/OWNER/NAME/pulls instead of search")

    for command, help_text in (("repo-counts", "open Issue/PR counts and default-branch commit counts"),
                               ("open-issues", "all open Issues across repositories (excluding PRs)")):
        p = sub.add_parser(command, help=help_text)
        p.add_argument("owner")
        p.add_argument("--org", action="store_true", help="OWNER is an organization")
        p.add_argument("--repos", default="", help="comma-separated repository names; bypass owner enumeration")

    p = sub.add_parser("pr-for-branch", help="PRs whose head is BRANCH (none -> exit 1)")
    p.add_argument("repo"); p.add_argument("branch", help="branch name, or owner:branch for a fork")
    p.add_argument("--state", choices=("open", "closed", "all"), default="all")

    p = sub.add_parser("pr-status", help="one-line PR state")
    p.add_argument("repo"); p.add_argument("number", type=int)

    p = sub.add_parser("pr-observe", help="snapshot PR/head/check/review activity for later diffing")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--min", type=int, default=1, dest="min_checks")

    p = sub.add_parser("pr-diff", help="compare two saved pr-observe JSON snapshots offline")
    p.add_argument("before"); p.add_argument("after")

    p = sub.add_parser("checks-wait", help="wait for check runs on a SHA and report conclusions/annotations")
    p.add_argument("repo"); p.add_argument("sha")
    p.add_argument("--min", type=int, default=1, dest="min_checks")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--interval", type=float, default=15.0)

    p = sub.add_parser("runs", help="list workflow runs by head SHA or workflow file")
    p.add_argument("repo")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha"); group.add_argument("--workflow")

    p = sub.add_parser("workflow-state", help="show a workflow's state (active, disabled_inactivity, ...)")
    p.add_argument("repo"); p.add_argument("workflow")

    p = sub.add_parser("workflow-dispatch", help="trigger workflow_dispatch (needs --write)")
    p.add_argument("repo"); p.add_argument("workflow")
    p.add_argument("--ref", required=True)
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("issue-comment", help="create a top-level PR conversation comment (needs --write)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--file", required=True, help="UTF-8 file containing the comment body (one trailing newline ignored)")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("pr-body-replace", help="replace an anchor that occurs exactly once (needs --write)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--old", required=True, help="file with the exact anchor text (one trailing newline ignored)")
    p.add_argument("--new", required=True, help="file with the replacement text (one trailing newline ignored)")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("pr-merge", help="verify head/mergeability/checks, then merge (needs --write)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--sha", required=True)
    p.add_argument("--min-checks", type=int, default=1)
    p.add_argument("--method", choices=MERGE_METHODS, default="squash")
    p.add_argument("--title")
    p.add_argument("--message-file")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("sync-main", help="merge origin/main into the checked-out PR branch (needs --write)")
    p.add_argument("--base", default="main"); p.add_argument("--remote", default="origin")
    p.add_argument("--push", action="store_true"); p.add_argument("--write", action="store_true")

    p = sub.add_parser("pr-body-set", help="replace the whole PR body with a file's text (needs --write)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--file", required=True, help="file with the full replacement body (one trailing newline ignored)")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("pr-edit", help="PATCH a PR's title/base/state (needs --write); draft/ready-for-review needs GraphQL, out of scope here")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--title"); p.add_argument("--base")
    p.add_argument("--state", choices=_PR_EDIT_STATES)
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("file-put", help="create or update one file via the contents API (needs --write)")
    p.add_argument("repo"); p.add_argument("path", help="path of the file inside the repository")
    p.add_argument("--from", dest="from_path", required=True, help="local file to read the new content from")
    p.add_argument("--branch", required=True)
    p.add_argument("--message", required=True, help="commit message")
    p.add_argument("--allow-default-branch", action="store_true",
                   help="permit writing directly to the repository's default branch")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("url", help="build a github.com / api.github.com URL for something; no network access")
    url_sub = p.add_subparsers(dest="url_kind", required=True)

    up = url_sub.add_parser("pr", help="a pull request, or one of its checks/files/commits tabs")
    up.add_argument("repo"); up.add_argument("number", type=int)
    up.add_argument("--tab", choices=("checks", "files", "commits"))

    up = url_sub.add_parser("compare", help="compare BASE...HEAD")
    up.add_argument("repo"); up.add_argument("base"); up.add_argument("head")

    up = url_sub.add_parser("blame", help="blame view of PATH at REF")
    up.add_argument("repo"); up.add_argument("ref"); up.add_argument("path")
    up.add_argument("--line", type=int)

    up = url_sub.add_parser("history", help="commit history of PATH at REF")
    up.add_argument("repo"); up.add_argument("ref"); up.add_argument("path")

    up = url_sub.add_parser("runs", help="workflow run list, optionally filtered by branch/event/status")
    up.add_argument("repo")
    up.add_argument("--workflow", help="workflow file name, e.g. ci.yml")
    up.add_argument("--branch"); up.add_argument("--event"); up.add_argument("--status")

    up = url_sub.add_parser("search", help="search results (pullrequests searches issues with is:pr added)")
    up.add_argument("query")
    up.add_argument("--type", choices=tuple(_SEARCH_ENDPOINTS), default="code")

    up = url_sub.add_parser("raw", help="raw file content of PATH at REF")
    up.add_argument("repo"); up.add_argument("ref"); up.add_argument("path")
    return parser


def run_command(args: argparse.Namespace, client: Client | None = None) -> dict:
    command = args.command
    if command in {"repo-counts", "open-issues"}:
        repos = tuple(name.strip() for name in args.repos.split(",") if name.strip())
        operation = repo_counts if command == "repo-counts" else open_issues
        return operation(args.owner, org=args.org, repos=repos, client=client)
    if command == "comments-file":
        show = tuple(int(part) for part in args.show.split(",") if part.strip()) if args.show else ()
        return comments_file(args.path, last=args.last, author=args.author, preview=args.preview, show=show)
    if command == "issue-comments":
        show = tuple(int(part) for part in args.show.split(",") if part.strip()) if args.show else ()
        return issue_comments(args.repo, args.number, since=args.since, last=args.last, author=args.author,
                              preview=args.preview, show=show, save=args.save, client=client)
    if command == "open-prs":
        repos = tuple(name.strip() for name in args.repos.split(",") if name.strip())
        return open_prs(args.owner, org=args.org, repos=repos, limit=args.limit, client=client)
    if command == "pr-for-branch":
        return pr_for_branch(args.repo, args.branch, state=args.state, client=client)
    if command == "pr-status":
        return pr_status(args.repo, args.number, client=client)
    if command == "pr-observe":
        return pr_observe(args.repo, args.number, min_checks=args.min_checks, client=client)
    if command == "pr-diff":
        return pr_observation_diff_files(args.before, args.after)
    if command == "checks-wait":
        return checks_wait(args.repo, args.sha, min_checks=args.min_checks, timeout=args.timeout,
                           interval=args.interval, client=client)
    if command == "runs":
        return runs(args.repo, sha=args.sha, workflow=args.workflow, client=client)
    if command == "workflow-state":
        return workflow_state(args.repo, args.workflow, client=client)
    if command == "workflow-dispatch":
        return workflow_dispatch(args.repo, args.workflow, ref=args.ref, write=args.write, client=client)
    if command == "issue-comment":
        return issue_comment_create(args.repo, args.number, body=_read_text(args.file),
                                    write=args.write, client=client)
    if command == "pr-body-replace":
        return pr_body_replace(args.repo, args.number, old=_read_text(args.old), new=_read_text(args.new),
                               write=args.write, client=client)
    if command == "pr-merge":
        message = _read_text(args.message_file) if args.message_file else None
        return pr_merge(args.repo, args.number, sha=args.sha, min_checks=args.min_checks, method=args.method,
                        title=args.title, message=message, write=args.write, client=client)
    if command == "sync-main":
        return sync_main(base=args.base, remote=args.remote, write=args.write, push=args.push)
    if command == "pr-body-set":
        return pr_body_set(args.repo, args.number, new=_read_text(args.file), write=args.write, client=client)
    if command == "pr-edit":
        return pr_edit(args.repo, args.number, title=args.title, base=args.base, state=args.state,
                       write=args.write, client=client)
    if command == "file-put":
        return file_put(args.repo, args.path, content=Path(args.from_path).read_bytes(), branch=args.branch,
                        message=args.message, write=args.write, allow_default_branch=args.allow_default_branch,
                        client=client)
    kind = args.url_kind
    if kind == "pr":
        return url_pr(args.repo, args.number, tab=args.tab)
    if kind == "compare":
        return url_compare(args.repo, args.base, args.head)
    if kind == "blame":
        return url_blame(args.repo, args.ref, args.path, line=args.line)
    if kind == "history":
        return url_history(args.repo, args.ref, args.path)
    if kind == "runs":
        return url_runs(args.repo, workflow=args.workflow, branch=args.branch, event=args.event, status=args.status)
    if kind == "search":
        return url_search(args.query, kind=args.type)
    return url_raw(args.repo, args.ref, args.path)


def main(argv: list[str] | None = None, *, client: Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_command(args, client)
    except (GhOpsError, OSError, ValueError) as error:
        print(scrub(f"error: {error}"), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2) if args.json else _render(args.command, result)
    print(scrub(text))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
