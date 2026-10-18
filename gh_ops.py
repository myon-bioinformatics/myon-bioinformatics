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
    python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main            # plan
    python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main --write    # apply, then read back
    python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main --write --allow-unmerged-commits
    python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main --write --allow-compare-failure
    python -S gh_ops.py file-put OWNER/REPO docs/x.md --from x.md --branch docs --message "add x" --write
    python -S gh_ops.py commit-files OWNER/REPO --branch docs --file a.md --file b.py=/tmp/b.py --delete old.md --message "m"
    python -S gh_ops.py git-commit --message-file msg.txt a.py --write
    python -S gh_ops.py git-commit --message-file msg.txt a.py --write --push
    python -S gh_ops.py subtree-push --prefix pythdragoras --remote https://github.com/OWNER/CHILD --branch sync/x
    python -S gh_ops.py subtree-push --prefix pythdragoras --remote https://github.com/OWNER/CHILD --branch sync/x --write
    python -S gh_ops.py url compare OWNER/REPO main feature   # no network; prints web + api URLs

Exit codes: 0 = OK, 1 = a checked condition was not met, 2 = input or
communication error.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import base64
import difflib
from datetime import datetime, timezone
import fnmatch
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
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

    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


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
            raise GhOpsError(scrub(text, (self._token,)), http_status=response.status)
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_ref_label(ref: str | None) -> str:
    return ref if ref else "(unknown)"


_BRANCH_NAME_INVALID = re.compile(r"[\000-\037\177 ~^:?*[\]\\]|@{|//")


def _validate_branch_segment(segment: str, *, field: str = "base") -> None:
    if not segment:
        raise GhOpsError(f"{field} must not contain empty path segments")
    if segment == "@":
        raise GhOpsError(f"{field} contains invalid characters for a branch name")
    if segment.startswith(".") or segment.endswith("."):
        raise GhOpsError(f"{field} must not start or end with .")
    if segment.startswith("-"):
        raise GhOpsError(f"{field} must not start with -")
    if segment.endswith(".lock"):
        raise GhOpsError(f"{field} must not end with .lock")
    if _BRANCH_NAME_INVALID.search(segment):
        raise GhOpsError(f"{field} contains invalid characters for a branch name")


def _validate_branch_name(name: str, *, field: str = "base") -> str:
    """Validate a branch name (subset of ``git check-ref-format`` rules, per ``/`` segment)."""
    branch = name.strip()
    if not branch:
        raise GhOpsError(f"{field} is required")
    if branch.startswith("refs/"):
        raise GhOpsError(f"{field} must be a branch name, not a refs/ path")
    if ".." in branch:
        raise GhOpsError(f"{field} must not contain ..")
    if branch.startswith("/") or branch.endswith("/"):
        raise GhOpsError(f"{field} must not start or end with /")
    if "//" in branch:
        raise GhOpsError(f"{field} must not contain //")
    for segment in branch.split("/"):
        _validate_branch_segment(segment, field=field)
    return branch


def _git_ref_tip_sha(client: Client, repo: str, branch: str) -> str:
    path = f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='')}"
    payload = client.get(path)
    sha = (payload.get("object") or {}).get("sha") or ""
    if not sha:
        raise GhOpsError(f"ref heads/{branch} returned no SHA")
    return sha


def _git_ref_tip_sha_optional(client: Client, repo: str, branch: str) -> str | None:
    try:
        return _git_ref_tip_sha(client, repo, branch)
    except GhOpsError:
        return None


def _git_commit_tree_sha(client: Client, repo: str, commit_sha: str) -> str:
    payload = client.get(f"/repos/{repo}/git/commits/{commit_sha}")
    return (payload.get("tree") or {}).get("sha") or ""


def _branch_tip_tree_match(client: Client, repo: str, base_branch: str, old_branch: str) -> dict:
    """Compare branch tips by tree SHA; empty trees never count as a match."""
    empty = {"matched": False, "new_base_tip_sha": None, "old_base_tip_sha": None,
             "new_base_tree_sha": None, "old_base_tree_sha": None}
    try:
        base_tip = _git_ref_tip_sha(client, repo, base_branch)
        old_tip = _git_ref_tip_sha(client, repo, old_branch)
        base_tree = _git_commit_tree_sha(client, repo, base_tip)
        old_tree = _git_commit_tree_sha(client, repo, old_tip)
        matched = bool(base_tree) and bool(old_tree) and base_tree == old_tree
        return {"matched": matched, "new_base_tip_sha": base_tip, "old_base_tip_sha": old_tip,
                "new_base_tree_sha": base_tree or None, "old_base_tree_sha": old_tree or None}
    except GhOpsError:
        return empty


def _pr_retarget_old_tip_stale(old_before: str | None, old_after: str | None, *, allow_both_missing: bool) -> str:
    if old_before is None and old_after is None:
        if allow_both_missing:
            return ""
        return "old base tip could not be verified before PATCH"
    if old_before == old_after:
        return ""
    if old_before is None or old_after is None:
        return "old base tip could not be verified consistently before PATCH"
    return (f"old base tip SHA changed from {old_before[:12]} to {old_after[:12]} "
            "before PATCH")


def _commit_message_subject(message: str | None, *, limit: int = 100) -> str:
    """First non-empty line of a commit message, or a fixed label when absent or whitespace-only."""
    if message is None:
        return "(empty commit message)"
    for line in message.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return "(empty commit message)"


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


def _pr_retarget_snapshot(pr: dict) -> dict:
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    return {"state": pr.get("state"), "merged": bool(pr.get("merged")),
            "base_ref": base.get("ref"), "base_sha": base.get("sha"), "head_sha": head.get("sha"),
            "url": pr.get("html_url")}


def _pr_retarget_stale_reason(before: dict, after: dict, *, expect_head_sha: str | None) -> str:
    if after.get("state") != "open":
        return "merged" if after.get("merged") else f"PR is {after.get('state')}"
    if after.get("base_ref") != before.get("base_ref"):
        return (f"base_ref changed from {before.get('base_ref')!r} to {after.get('base_ref')!r} "
                "before PATCH")
    if after.get("head_sha") != before.get("head_sha"):
        return (f"head SHA changed from {(before.get('head_sha') or '')[:12]} "
                f"to {(after.get('head_sha') or '')[:12]} before PATCH")
    if expect_head_sha is not None:
        prefix = _sha(expect_head_sha)
        head = (after.get("head_sha") or "").lower()
        if not head.startswith(prefix):
            return f"head SHA {head[:12]} does not match --expect-head-sha {expect_head_sha}"
    return ""


def pr_retarget(repo: str, number: int, base: str = "main", *, write: bool = False,
                allow_unmerged_commits: bool = False, allow_compare_failure: bool = False,
                expect_head_sha: str | None = None, client: Client | None = None) -> dict:
    """Change a PR's base branch (for example a stacked PR onto main after its parent merged).

    Dry-run by default. Compares the old base with the new one: commits on the old
    base that are not in the new base would join the PR diff, so ``--write`` is
    refused unless ``--allow-unmerged-commits``. A compare failure (for example a
    deleted old base) needs ``--allow-compare-failure``. Immediately before PATCH,
    the PR and old-base tip SHAs are re-read and must still match the compare-time
    snapshot. Verifies by re-reading the PR after PATCH.
    """
    if int(number) < 1:
        raise GhOpsError("PR number must be a positive integer")
    if not isinstance(base, str):
        raise GhOpsError("base is required")
    base = _validate_branch_name(base)
    if expect_head_sha is not None:
        _sha(expect_head_sha)
    client = _client(client)
    repo = _repo(repo)
    number = int(number)
    path = f"/repos/{repo}/pulls/{number}"

    def pull_snapshot() -> dict:
        return _pr_retarget_snapshot(client.get(path))

    head = pull_snapshot()
    old = head["base_ref"]
    out: dict[str, Any] = {"ok": True, "schema": "gh-ops-pr-retarget/1", "repository": repo, "number": number,
                           "from": old, "to": base, "write": bool(write), "url": head.get("url"),
                           "observed_at": _utc_now_iso()}
    if head["state"] != "open":
        out["status"] = "not_open"
        out["ok"] = False
        out["reason"] = "merged" if head["merged"] else f"PR is {head['state']}"
        return out
    if old == base:
        out["status"] = "already_satisfied"
        out["reason"] = "base already matches"
        return out
    old_tip_sha = _git_ref_tip_sha_optional(client, repo, old) if old else None
    if old:
        out["old_base_tip_sha"] = old_tip_sha
    compare_path = (f"/repos/{repo}/compare/{urllib.parse.quote(base, safe='')}"
                    f"...{urllib.parse.quote(old or '', safe='')}")
    compare_failed = False
    ahead_by = 0
    try:
        cmp = client.get(compare_path)
        ahead_raw = cmp.get("ahead_by")
        if ahead_raw is None:
            compare_failed = True
            out["old_base_unmerged_commits"] = None
            out["old_base_commit_subjects"] = []
            out["compare_error"] = ("missing ahead_by in compare response" if "ahead_by" not in cmp
                                    else "null ahead_by in compare response")
        else:
            try:
                ahead_by = int(ahead_raw)
            except (TypeError, ValueError):
                compare_failed = True
                out["old_base_unmerged_commits"] = None
                out["old_base_commit_subjects"] = []
                out["compare_error"] = "invalid ahead_by in compare response"
            else:
                if ahead_by < 0:
                    compare_failed = True
                    out["old_base_unmerged_commits"] = None
                    out["old_base_commit_subjects"] = []
                    out["compare_error"] = "negative ahead_by in compare response"
                else:
                    out["old_base_unmerged_commits"] = ahead_by
                    commits = cmp.get("commits") or []
                    out["old_base_commit_subjects"] = [
                        _commit_message_subject((item.get("commit") or {}).get("message"))
                        for item in commits[:5]]
    except GhOpsError as error:
        compare_failed = True
        out["old_base_unmerged_commits"] = None
        out["old_base_commit_subjects"] = []
        out["compare_error"] = "not_found" if error.http_status == 404 else str(error)
    unmerged_risky = ahead_by > 0
    if unmerged_risky and old:
        tree_info = _branch_tip_tree_match(client, repo, base, old)
        out["tree_compare"] = tree_info
        if tree_info["matched"]:
            unmerged_risky = False
            out["unmerged_commit_tree_match"] = True
    old_label = _base_ref_label(old)
    if compare_failed:
        out["warning"] = f"could not compare {old_label} with {base}"
    elif unmerged_risky:
        out["warning"] = f"{old_label} has {ahead_by} commit(s) not in {base}; they would join the diff"
    if not write:
        out["status"] = "planned"
        out["dry_run"] = True
        return out
    if compare_failed and not allow_compare_failure:
        out["status"] = "refused_compare_failure"
        out["ok"] = False
        out["reason"] = out["warning"]
        return out
    if unmerged_risky and not allow_unmerged_commits:
        out["status"] = "refused_unmerged_base"
        out["ok"] = False
        out["reason"] = out.get("warning", "")
        return out
    fresh = pull_snapshot()
    old_tip_fresh = _git_ref_tip_sha_optional(client, repo, old) if old else None
    allow_missing_tips = compare_failed and allow_compare_failure
    tip_stale = _pr_retarget_old_tip_stale(old_tip_sha, old_tip_fresh, allow_both_missing=allow_missing_tips)
    if tip_stale:
        out["status"] = "refused_stale_pr"
        out["ok"] = False
        out["reason"] = tip_stale
        return out
    stale = _pr_retarget_stale_reason(head, fresh, expect_head_sha=expect_head_sha)
    if stale:
        out["status"] = "refused_stale_pr"
        out["ok"] = False
        out["reason"] = stale
        return out
    client.request("PATCH", path, {"base": base})
    after = pull_snapshot()
    verified = after["base_ref"] == base
    out["status"] = "verified" if verified else "verification_mismatch"
    out["ok"] = verified
    out["dry_run"] = False
    if not verified:
        out["reason"] = f"base is {after['base_ref']!r} after PATCH, expected {base!r}"
    return out


def pr_merge(repo: str, number: int, *, sha: str, min_checks: int = 1, method: str = "squash",
             message: str | None = None, title: str | None = None, ignore: str | None = None,
             write: bool = False, client: Client | None = None) -> dict:
    """Verify, then (only with ``write=True``) merge a PR in one call.

    Preconditions: open, ``mergeable_state == "clean"``, head matches
    ``sha`` (an abbreviated SHA must match exactly one PR commit), and the
    head has at least ``min_checks`` check runs, all completed and passing.
    Any failed precondition returns ``ok: False`` without an HTTP write.
    The merge request always pins ``sha`` to the verified head. ``ignore`` is a
    regex of check names (e.g. ``(?i)copilot`` for the review bot) dropped
    before judging; they are listed under ``checks["ignored"]``.
    """
    if method not in MERGE_METHODS:
        raise GhOpsError(f"--method must be one of {', '.join(MERGE_METHODS)}")
    if min_checks < 1:
        raise GhOpsError("--min-checks must be at least 1; zero check runs never counts as green")
    try:
        pattern = re.compile(ignore) if ignore else None
    except re.error as error:
        raise GhOpsError(f"--ignore is not a valid regex: {error}") from None
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
        runs = _check_runs(client, repo, head)
        ignored = [run for run in runs if pattern and pattern.search(run.get("name") or "")]
        checks = _summarize_checks([run for run in runs if run not in ignored], min_checks)
        checks["ignored"] = [{"name": run.get("name"), "conclusion": run.get("conclusion")} for run in ignored]
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


_COMMIT_FILES_MAX = 100
_COMMIT_FILE_BYTES = 10 * 1024 * 1024


def _git_blob_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _commit_files_path(path: Any) -> str:
    if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path
            or any(part in ("", ".", "..", ".git") for part in path.split("/"))):
        raise GhOpsError(f"invalid repository path: {path!r}")
    return path


def commit_files(repo: str, branch: str, files: dict, message: str, *, deletes: tuple[str, ...] = (),
                 expect_head: str | None = None, write: bool = False, allow_default_branch: bool = False,
                 client: Client | None = None) -> dict:
    """Commit several files to ``branch`` in ONE commit through the Git Data API (no clone).

    The blobs -> tree -> commit -> ref sequence of a connector ``create_tree``
    call, planned first. A dry run lists each path as ``new``, ``changed``,
    ``unchanged``, ``delete`` or ``absent`` with its size and Git blob SHA, and
    issues no write. Like ``file-put`` it refuses the default branch unless
    ``allow_default_branch``. ``expect_head`` refuses if the branch moved; the
    ref update is fast-forward only (``force: false``), and unchanged blobs are
    not uploaded. After writing it re-reads the branch and the new tree and
    compares every blob SHA with the local bytes.
    """
    client, repo = _client(client), _repo(repo)
    branch = _validate_branch_name(branch, field="branch")
    if not isinstance(message, str) or not message.strip():
        raise GhOpsError("commit message is required")
    files = {_commit_files_path(path): data for path, data in dict(files).items()}
    deletes = tuple(_commit_files_path(path) for path in deletes)
    if not files and not deletes:
        raise GhOpsError("nothing to commit")
    if len(files) + len(deletes) > _COMMIT_FILES_MAX:
        raise GhOpsError(f"at most {_COMMIT_FILES_MAX} paths per commit")
    if set(files) & set(deletes):
        raise GhOpsError("a path cannot be both written and deleted")
    for path, data in files.items():
        if not isinstance(data, bytes):
            raise GhOpsError(f"{path}: content must be bytes")
        if len(data) > _COMMIT_FILE_BYTES:
            raise GhOpsError(f"{path}: larger than {_COMMIT_FILE_BYTES} bytes")
    if expect_head is not None:
        # A full SHA, compared exactly: a short prefix would let a moved branch through.
        if not isinstance(expect_head, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", expect_head):
            raise GhOpsError(f"expect_head must be the full 40-hex commit SHA, got {expect_head!r}")
        expect_head = expect_head.lower()
    default_branch = client.get(f"/repos/{repo}")["default_branch"]
    out: dict = {"ok": False, "dry_run": not write, "repository": repo, "branch": branch}
    if branch == default_branch and not allow_default_branch:
        return {**out, "reason": f"refusing to write to the default branch {default_branch!r} "
                                 "without --allow-default-branch"}
    head = _git_ref_tip_sha(client, repo, branch)
    out["head"] = head
    if expect_head is not None and head != expect_head:
        return {**out, "reason": f"branch moved: head is {head[:12]}, expected {expect_head}"}
    base_tree = _git_commit_tree_sha(client, repo, head)
    tree = client.get(f"/repos/{repo}/git/trees/{base_tree}", params={"recursive": "1"})
    existing = None if tree.get("truncated") else {
        entry["path"]: entry for entry in tree.get("tree") or [] if entry.get("type") == "blob"}
    plan = []
    for path, data in sorted(files.items()):
        sha = _git_blob_sha(data)
        before = None if existing is None else existing.get(path)
        change = ("unknown" if existing is None else "new" if before is None
                  else "unchanged" if before.get("sha") == sha else "changed")
        plan.append({"path": path, "bytes": len(data), "blob_sha": sha, "change": change,
                     "mode": (before or {}).get("mode", "100644")})
    for path in sorted(deletes):
        plan.append({"path": path, "delete": True,
                     "change": "unknown" if existing is None else "delete" if path in existing else "absent"})
    out["plan"] = plan
    if all(item["change"] in ("unchanged", "absent") for item in plan):
        return {**out, "ok": True, "changed": False, "reason": "unchanged"}
    if not write:
        return {**out, "ok": True, "changed": False}
    elements = []
    for item in plan:
        if item.get("delete"):
            if item["change"] != "absent":
                elements.append({"path": item["path"], "mode": "100644", "type": "blob", "sha": None})
            continue
        if item["change"] == "unchanged":
            continue
        blob = client.request("POST", f"/repos/{repo}/git/blobs",
                              {"content": base64.b64encode(files[item["path"]]).decode("ascii"),
                               "encoding": "base64"}, expect=(201,)).data
        if (blob or {}).get("sha") != item["blob_sha"]:
            return {**out, "reason": f"blob SHA mismatch for {item['path']}"}
        elements.append({"path": item["path"], "mode": item["mode"], "type": "blob", "sha": item["blob_sha"]})
    new_tree = client.request("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": elements},
                              expect=(201,)).data["sha"]
    commit = client.request("POST", f"/repos/{repo}/git/commits",
                            {"message": message, "tree": new_tree, "parents": [head]}, expect=(201,)).data["sha"]
    out["commit_sha"] = commit
    try:
        client.request("PATCH", f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch, safe='')}",
                       {"sha": commit, "force": False})
    except GhOpsError as error:
        return {**out, "reason": f"ref update refused ({error}); the branch probably moved. The new commit is "
                                 "unreferenced and harmless: re-plan and retry"}
    after = _git_ref_tip_sha(client, repo, branch)
    check = client.get(f"/repos/{repo}/git/trees/{new_tree}", params={"recursive": "1"})
    entries = {entry["path"]: entry.get("sha") for entry in check.get("tree") or [] if entry.get("type") == "blob"}
    verified = (after == commit
                and all(entries.get(item["path"]) == item["blob_sha"] for item in plan if not item.get("delete"))
                and all(item["path"] not in entries for item in plan if item.get("delete")))
    return {**out, "ok": verified, "changed": True, "verified": verified,
            **({} if verified else {"reason": "branch or tree does not match after the write"})}


_GIT_COMMIT_NETWORK_HINTS = (
    "could not resolve host", "connection timed out", "connection refused", "network is unreachable",
    "temporary failure in name resolution", "could not read from remote repository",
    "the remote end hung up unexpectedly", "early eof", "rpc failed", "failed to connect",
    "operation timed out", "connection reset", "ssl_read", "no route to host", "socket is not connected",
)
_GIT_COMMIT_PUSH_DELAYS: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)


def _git_commit_normalize(path: str) -> str:
    if os.path.isabs(path):
        path = os.path.relpath(path)
    return Path(os.path.normpath(path)).as_posix()


def git_commit_staged_files(*, cwd: str = ".", run: Callable[..., Any] = subprocess.run) -> list[str]:
    done = run(["git", "diff", "--cached", "--name-only"], cwd=cwd, capture_output=True, text=True)
    return [line for line in done.stdout.splitlines() if line]


def git_commit_read_message(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\n")


def git_commit_build_message(base: str, trailers: list[str]) -> str:
    body = base.rstrip("\n")
    seen = {line.strip() for line in body.splitlines()}
    to_add = []
    for trailer in trailers:
        key = trailer.strip()
        if key in seen:
            continue
        to_add.append(trailer)
        seen.add(key)
    if not to_add:
        return body + "\n"
    return body + "\n\n" + "\n".join(to_add) + "\n"


# fetch-guard: a network git command must never wait for a person (credential or SSH
# prompt) and must end within a deadline, so a missing token fails fast instead of hanging.
_GIT_NETWORK_COMMANDS = frozenset({"fetch", "push", "pull", "clone", "ls-remote"})
_GIT_NETWORK_TIMEOUT = 300.0
_GIT_PUSH_ATTEMPT_TIMEOUT = 60.0


def _git_network_env(extra: dict | None = None) -> dict:
    # Apply guards last: callers may add credentials, but cannot re-enable prompts.
    # Empty GIT_ASKPASS also suppresses core.askPass (TERMINAL_PROMPT alone does not).
    env = {**os.environ, **(extra or {}), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "GIT_ASKPASS": "", "SSH_ASKPASS": "", "SSH_ASKPASS_REQUIRE": "never"}
    return env


def _git_network_run(argv: list[str], *, run: Callable[..., Any], timeout: float,
                     cwd: str, env: dict) -> Any:
    """Bound network Git and its transport children; keep injectable runners intact."""
    kwargs = {"cwd": cwd, "capture_output": True, "text": True, "stdin": subprocess.DEVNULL,
              "timeout": timeout, "env": env}
    if run is not subprocess.run:
        return run(argv, **kwargs)
    if len(argv) > 1 and argv[0] == "git" and argv[1] in _GIT_NETWORK_COMMANDS and "GIT_SSH_COMMAND" not in env:
        # Git's priority is GIT_SSH_COMMAND > core.sshCommand > GIT_SSH > ssh.
        # Resolve config in the command's cwd/env; never replace a configured
        # identity, port or wrapper. This setup read consumes the communication
        # budget; standalone/local Git operations gain no new restrictions.
        started = time.monotonic()
        config = _git_network_run(["git", "config", "--get", "core.sshCommand"],
                                  run=run, timeout=timeout, cwd=cwd, env=env)
        if config.returncode not in (0, 1):
            return subprocess.CompletedProcess(argv, config.returncode, config.stdout, config.stderr)
        if config.returncode == 1 and not env.get("GIT_SSH"):
            kwargs["env"] = {**env, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        timeout = remaining
    # subprocess.run kills only Git on timeout. SSH/credential helpers can retain
    # output pipes, making communicate() (or Windows reader-thread cleanup) hang.
    # Regular temporary files let us wait for the process without draining pipes.
    kwargs.pop("capture_output")
    kwargs.pop("timeout")
    with tempfile.TemporaryFile(mode="w+") as stdout, tempfile.TemporaryFile(mode="w+") as stderr, \
         subprocess.Popen(argv, stdout=stdout, stderr=stderr,
                          start_new_session=(os.name == "posix"), **kwargs) as process:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                # taskkill is the Windows process-tree equivalent. Its own wait
                # is bounded too, and it has no output pipes to drain.
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                process.kill()
            process.wait(timeout=1)
            raise error
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(argv, process.returncode, stdout.read(), stderr.read())


def _git_commit_looks_like_network_error(text: str) -> bool:
    lowered = text.lower()
    # SSH authentication failures also contain the broad remote-read hint below.
    if any(hint in lowered for hint in ("permission denied", "authentication failed", "terminal prompts disabled",
                                       "host key verification failed", "[rejected]", "non-fast-forward")):
        return False
    return any(hint in lowered for hint in _GIT_COMMIT_NETWORK_HINTS)


def git_commit_push_with_retry(*, cwd: str = ".", delays: tuple[float, ...] = _GIT_COMMIT_PUSH_DELAYS,
                               run: Callable[..., Any] = subprocess.run,
                               sleep: Callable[[float], None] = time.sleep,
                               push_args: tuple[str, ...] = ("origin", "HEAD"),
                               timeout: float = _GIT_PUSH_ATTEMPT_TIMEOUT,
                               total_timeout: float = _GIT_NETWORK_TIMEOUT,
                               monotonic: Callable[[], float] = time.monotonic) -> tuple[bool, str]:
    """``git push <push_args>``, retrying only on network-looking errors (default: ``origin HEAD``).

    ``timeout`` bounds one attempt (60s); ``total_timeout`` bounds all attempts
    and backoff together (300s). Process cleanup may add at most 1s on POSIX,
    2s on Windows, plus OS scheduling/process creation overhead. Neither
    limit applies to local add/commit/merge or subtree splitting.
    """
    for name, value in (("timeout", timeout), ("total_timeout", total_timeout)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < float("inf"):
            raise ValueError(f"{name} must be positive and finite")
    remaining = list(delays)
    if any(isinstance(d, bool) or not isinstance(d, (int, float)) or not 0 <= d < float("inf") for d in remaining):
        raise ValueError("delays must be non-negative and finite")
    deadline = monotonic() + total_timeout
    output = ""

    def exhausted() -> tuple[bool, str]:
        return False, output + f"\ngit push: total timeout after {total_timeout:g}s (attempts and retry waits)"

    while True:
        budget = deadline - monotonic()
        if budget <= 0:
            return exhausted()
        attempt_timeout = min(timeout, budget)
        try:
            done = _git_network_run(["git", "push", *push_args], run=run, cwd=cwd,
                                    timeout=attempt_timeout, env=_git_network_env())
            output = done.stdout + done.stderr
        except subprocess.TimeoutExpired:
            done, output = None, f"git push: operation timed out after {attempt_timeout:g}s"
        if done is not None and done.returncode == 0:
            return True, output
        if deadline - monotonic() <= 0:
            return exhausted()
        if not remaining or not _git_commit_looks_like_network_error(output):
            return False, output
        budget = deadline - monotonic()
        if budget <= 0:
            return exhausted()
        delay = remaining.pop(0)
        sleep(min(delay, budget))


def git_commit(message_file: str, files: list[str], *, trailers: list[str] | None = None,
               allow_staged: bool = False, push: bool = False, write: bool = False, cwd: str = ".",
               run: Callable[..., Any] = subprocess.run,
               sleep: Callable[[float], None] = time.sleep) -> dict:
    """Stage an explicit file list and commit (local git; migrated from browser-test-kit git_commit.py)."""
    trailers = list(trailers or [])
    message_path = Path(message_file)
    if not message_path.is_file():
        raise GhOpsError(f"message file not found: {message_file}")
    base_message = git_commit_read_message(message_path)
    if not base_message.strip():
        raise GhOpsError(f"message file is empty: {message_file}")

    wanted = {_git_commit_normalize(path) for path in files}
    if not allow_staged:
        unexpected = sorted(path for path in git_commit_staged_files(cwd=cwd, run=run)
                            if _git_commit_normalize(path) not in wanted)
        if unexpected:
            raise GhOpsError("files other than FILE... are already staged (use --allow-staged): "
                             + ", ".join(unexpected))

    if not write:
        return {"ok": True, "dry_run": True, "files": list(files), "push": push, "message_file": message_file}

    added = run(["git", "add", "--", *files], cwd=cwd, capture_output=True, text=True)
    if added.returncode != 0:
        return {"ok": False, "reason": (added.stdout + added.stderr).strip() or "git add failed"}

    transcript = run(["git", "status", "--short"], cwd=cwd, capture_output=True, text=True).stdout
    transcript += run(["git", "diff", "--cached", "--stat"], cwd=cwd, capture_output=True, text=True).stdout

    message = git_commit_build_message(base_message, trailers)
    commit = run(["git", "commit", "--file", "-"], cwd=cwd, capture_output=True, text=True, input=message)
    if commit.returncode != 0:
        return {"ok": False, "reason": (commit.stdout + commit.stderr).strip() or "git commit failed"}

    sha = run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()
    transcript += sha + "\n"
    pushed = False
    if push:
        ok, output = git_commit_push_with_retry(cwd=cwd, run=run, sleep=sleep)
        transcript += output
        if not ok:
            return {"ok": False, "sha": sha, "transcript": transcript, "reason": "git push failed"}
        pushed = True
    return {"ok": True, "sha": sha, "pushed": pushed, "transcript": transcript}


def sync_main(*, base: str = "main", remote: str = "origin", write: bool = False, push: bool = False,
              cwd: str = ".", run: Callable[..., Any] = subprocess.run) -> dict:
    """Merge ``remote/base`` into the checked-out PR branch (git, no REST).

    Refuses when the tree is dirty or local HEAD differs from the remote
    branch; aborts the merge on conflict. ``push`` pushes only after a clean
    merge and only with ``write``.
    """
    def git(*args: str, check: bool = True) -> Any:
        extra = ({"timeout": _GIT_NETWORK_TIMEOUT, "env": _git_network_env()}
                 if args[0] in _GIT_NETWORK_COMMANDS else {})
        try:
            if extra:
                completed = _git_network_run(["git", *args], run=run, cwd=cwd, **extra)
            else:
                completed = run(["git", *args], cwd=cwd, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise GhOpsError(f"git {args[0]} timed out after {_GIT_NETWORK_TIMEOUT:g}s") from None
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


# --- subtree push: verified split SHA, new branch only (ported from gh_identity#77) ---------
#
# Decision logic ported from gh_identity#77 (branch cursor/subtree-stale-split-4489, head
# d43ceb5): tree-validated split, filter-branch fallback, push guards, direct push of the
# verified SHA (never `git subtree push`, which re-splits internally), post-push check.
# TODO(gh_identity#77): once #77 is merged and vendor/gh_identity.py is repinned to it,
# replace the read-only helpers below (_subtree_prefix .. _subtree_push_guards) with GHI's
# public read-only functions and keep only the push + post-push verification here. Until
# then everything here fails closed: any unexpected git result refuses the push.

_SUBTREE_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SUBTREE_PROTECTED_BRANCHES = ("main", "master")
_SUBTREE_COMPARE_FALLBACKS = ("main", "master", "trunk")
_SUBTREE_TRAILER = "Subtree-Split"


class SubtreePushError(GhOpsError):
    """A subtree-push precondition or verification failed (``code`` mirrors gh_identity#77)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code + (f": {detail}" if detail else ""))


def _subtree_git(cwd: Any, args: list[str], *, run: Callable[..., Any], timeout: float,
                 ok: tuple[int, ...] = (0,), env: dict | None = None) -> Any:
    kwargs: dict = {"cwd": str(cwd), "capture_output": True, "text": True, "timeout": timeout}
    if args and args[0] in _GIT_NETWORK_COMMANDS:
        kwargs["env"] = _git_network_env(env)
    elif env:
        kwargs["env"] = {**os.environ, **env}
    try:
        if args and args[0] in _GIT_NETWORK_COMMANDS:
            done = _git_network_run(["git", *args], run=run, cwd=str(cwd), timeout=timeout, env=kwargs["env"])
        else:
            done = run(["git", *args], **kwargs)
    except subprocess.TimeoutExpired:
        raise SubtreePushError("git_timeout", f"git {args[0]}") from None
    if done.returncode not in ok:
        tail = (done.stderr or "").strip()[-300:]
        raise SubtreePushError("git_failed", f"git {args[0]} exited {done.returncode}" + (f": {tail}" if tail else ""))
    return done


def _subtree_prefix(prefix: Any) -> str:
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("prefix required")
    if any(c in prefix for c in ("\0", "\n", "\r")) or prefix.startswith("-"):
        raise ValueError("invalid prefix")
    if os.path.isabs(prefix) or prefix.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", prefix):
        raise ValueError("prefix must be relative to the repository top level")
    parts = [part for part in prefix.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or parts[0] == ".git":
        raise ValueError("prefix must stay inside the repository")
    if any(part.startswith("-") for part in parts):
        raise ValueError("invalid prefix")
    return "/".join(parts)


def _subtree_branch(branch: Any) -> str:
    if (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or ".." in branch or "//" in branch or branch.endswith(("/", ".", ".lock"))
            or any(part.startswith(".") for part in branch.split("/"))):
        raise ValueError("invalid branch")
    return branch


def _subtree_remote(remote: Any) -> str:
    if remote is None:
        raise ValueError("remote required")
    if not isinstance(remote, str) or not remote or "\n" in remote or remote.startswith("-"):
        raise ValueError("invalid remote")
    if remote.startswith("/"):
        return remote
    parts = urllib.parse.urlsplit(remote)
    if parts.scheme not in ("https", "file"):
        raise ValueError("remote must be https://, file:// or an absolute path")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("remote must not carry credentials")
    return remote


def _subtree_revision(revision: Any) -> str:
    if (not isinstance(revision, str) or not revision or revision.startswith("-")
            or any(c in revision for c in ("\0", "\n", "\r"))):
        raise ValueError("invalid revision")
    return revision


def _subtree_oid(cwd: Any, spec: str, *, run: Callable[..., Any], timeout: float) -> str | None:
    """Resolve a tree/commit oid (e.g. ``SHA:prefix`` or ``SHA^{tree}``); None when absent."""
    if not isinstance(spec, str) or not spec or spec.startswith("-") or any(c in spec for c in ("\0", "\n", "\r")):
        return None
    done = _subtree_git(cwd, ["rev-parse", "-q", "--verify", spec], run=run, timeout=timeout, ok=(0, 1, 128))
    if done.returncode != 0:
        return None
    oid = done.stdout.strip()
    return oid if _SUBTREE_OID_RE.fullmatch(oid) else None


def _subtree_commit_exists(cwd: Any, sha: str | None, *, run: Callable[..., Any], timeout: float) -> bool:
    if not sha or not _SUBTREE_OID_RE.fullmatch(sha):
        return False
    done = _subtree_git(cwd, ["cat-file", "-t", sha], run=run, timeout=timeout, ok=(0, 1, 128))
    return done.returncode == 0 and done.stdout.strip() == "commit"


def _subtree_split_matches_prefix_tree(cwd: Any, source: str, prefix: str, split_sha: str | None, *,
                                       run: Callable[..., Any], timeout: float, split_root: Any = None) -> dict:
    """``trees_match`` is True only when ``split_sha^{tree}`` equals ``source:<prefix>``."""
    prefix_tree = _subtree_oid(cwd, f"{source}:{prefix}", run=run, timeout=timeout)
    split_tree = (_subtree_oid(cwd if split_root is None else split_root, f"{split_sha}^{{tree}}", run=run,
                               timeout=timeout) if split_sha else None)
    return {"prefix_tree": prefix_tree, "split_tree": split_tree,
            "trees_match": bool(prefix_tree and split_tree and prefix_tree == split_tree)}


def _subtree_is_ancestor(cwd: Any, ancestor: str, descendant: str, *, run: Callable[..., Any],
                         timeout: float) -> bool:
    if not (_subtree_commit_exists(cwd, ancestor, run=run, timeout=timeout)
            and _subtree_commit_exists(cwd, descendant, run=run, timeout=timeout)):
        return False
    done = _subtree_git(cwd, ["merge-base", "--is-ancestor", ancestor, descendant], run=run, timeout=timeout,
                        ok=(0, 1))
    return done.returncode == 0


def _subtree_tree_paths(cwd: Any, tree: str, *, run: Callable[..., Any], timeout: float) -> set[str]:
    done = _subtree_git(cwd, ["ls-tree", "-r", "-z", "--name-only", tree], run=run, timeout=timeout)
    return {path for path in done.stdout.split("\0") if path}


def _subtree_available(cwd: Any, *, run: Callable[..., Any], timeout: float) -> bool:
    done = _subtree_git(cwd, ["--exec-path"], run=run, timeout=timeout, ok=tuple(range(256)))
    exec_path = done.stdout.strip() if done.returncode == 0 else ""
    if exec_path and os.path.isfile(os.path.join(exec_path, "git-subtree")):
        return True
    return shutil.which("git-subtree") is not None


@contextlib.contextmanager
def _subtree_cache_cleanup(cwd: Any, *, run: Callable[..., Any], timeout: float):
    """git-subtree leaves $GIT_DIR/subtree-cache/<pid>; remove only what this call created."""
    git_dir = Path(_subtree_git(cwd, ["rev-parse", "--absolute-git-dir"], run=run, timeout=timeout).stdout.strip())
    cache = git_dir / "subtree-cache"
    existed = cache.is_dir()
    before = set(os.listdir(cache)) if existed else set()
    try:
        yield
    finally:
        if cache.is_dir() and not cache.is_symlink():
            for name in set(os.listdir(cache)) - before:
                shutil.rmtree(cache / name, ignore_errors=True)
            if not existed:
                with contextlib.suppress(OSError):
                    cache.rmdir()


def _subtree_split_filter_branch(cwd: Any, prefix: str, source: str, *, run: Callable[..., Any],
                                 timeout: float) -> str | None:
    """filter-branch in a temporary clone; import the result object-only (no refs, no FETCH_HEAD)."""
    origin = _subtree_git(cwd, ["rev-parse", "--show-toplevel"], run=run, timeout=timeout).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="gh-ops-subtree-fb-") as directory:
        clone = os.path.join(directory, "repo")
        steps = (
            (cwd, ["clone", "-q", "--no-hardlinks", origin, clone]),
            (clone, ["fetch", "-q", "--no-write-fetch-head", "--no-tags", origin, source]),
            (clone, ["checkout", "-q", "--detach", source]),
            (clone, ["filter-branch", "-f", "--prune-empty", "--subdirectory-filter", prefix, "HEAD"]),
        )
        for where, args in steps:
            env = {"FILTER_BRANCH_SQUELCH_WARNING": "1"} if args[0] == "filter-branch" else None
            if _subtree_git(where, args, run=run, timeout=timeout, ok=(0, 1, 128), env=env).returncode != 0:
                return None
        fb = _subtree_oid(clone, "HEAD^{commit}", run=run, timeout=timeout)
        if not fb or not _subtree_split_matches_prefix_tree(cwd, source, prefix, fb, run=run, timeout=timeout,
                                                            split_root=clone)["trees_match"]:
            return None
        done = _subtree_git(cwd, ["fetch", "-q", "--no-write-fetch-head", "--no-tags", clone, fb], run=run,
                            timeout=timeout, ok=(0, 1, 128))
        if done.returncode != 0 or not _subtree_split_matches_prefix_tree(
                cwd, source, prefix, fb, run=run, timeout=timeout)["trees_match"]:
            return None
        return fb


def subtree_split_verified(prefix: str, *, source: str = "HEAD", cwd: str = ".",
                           run: Callable[..., Any] = subprocess.run, timeout: float = 600.0) -> dict:
    """Read-only ``git subtree split`` whose result must have the tree of ``source:<prefix>``.

    ``git subtree split`` can exit 0 with a stale SHA (seen on git 2.47.3); then a
    filter-branch split in a temporary clone is tried. Never passes --branch/--rejoin, so no
    ref moves. ``sha`` is None (and ``reason`` set) when no tree-identical split exists.
    """
    prefix = _subtree_prefix(prefix)
    source = _subtree_oid(cwd, f"{_subtree_revision(source)}^{{commit}}", run=run, timeout=timeout)
    if not source:
        raise ValueError("revision not found")
    base = {"prefix": prefix, "source": source, "fallback_used": False}
    kind = _subtree_git(cwd, ["cat-file", "-t", f"{source}:{prefix}"], run=run, timeout=timeout, ok=(0, 1, 128))
    if kind.returncode != 0 or kind.stdout.strip() != "tree":
        return {**base, "sha": None, "reason": "prefix_not_found"}
    if not _subtree_available(cwd, run=run, timeout=timeout):
        return {**base, "sha": None, "reason": "subtree_unavailable"}
    with _subtree_cache_cleanup(cwd, run=run, timeout=timeout):
        done = _subtree_git(cwd, ["subtree", "split", "-q", f"--prefix={prefix}", source], run=run,
                            timeout=timeout, ok=tuple(range(256)))
    if done.returncode != 0:
        return {**base, "sha": None, "reason": "subtree_split_failed",
                "detail": (done.stderr or "").strip()[-300:]}
    lines = done.stdout.strip().splitlines()
    subtree_sha = lines[-1].strip() if lines else ""
    if not _SUBTREE_OID_RE.fullmatch(subtree_sha):
        return {**base, "sha": None, "reason": "subtree_split_failed", "detail": "unexpected git subtree output"}
    check = _subtree_split_matches_prefix_tree(cwd, source, prefix, subtree_sha, run=run, timeout=timeout)
    method, warning, sha = "subtree", None, subtree_sha
    if not check["trees_match"]:
        warning = "subtree_split_stale"
        base["fallback_used"] = True
        fb = _subtree_split_filter_branch(cwd, prefix, source, run=run, timeout=timeout)
        if fb is None:
            return {**base, "sha": None, "reason": "stale_subtree_split", "subtree_sha": subtree_sha,
                    "warning": warning, "method": "subtree", **check}
        method, sha = "filter-branch", fb
    final = _subtree_split_matches_prefix_tree(cwd, source, prefix, sha, run=run, timeout=timeout)
    if not final["trees_match"]:
        return {**base, "sha": None, "reason": "stale_subtree_split", "subtree_sha": subtree_sha,
                "warning": warning or "subtree_split_stale", "method": method, **final}
    return {**base, "sha": sha, "reason": None, "method": method, "warning": warning,
            "subtree_sha": subtree_sha if method != "subtree" else None, **final}


def _subtree_ls_remote(cwd: Any, remote: str, refs: list[str], *, run: Callable[..., Any],
                       timeout: float) -> tuple[str | None, dict]:
    """One ``ls-remote --symref``: (remote default branch, {exact ref: tip}). Fails closed."""
    done = _subtree_git(cwd, ["ls-remote", "--symref", "--", remote, "HEAD", *refs], run=run, timeout=timeout,
                        ok=tuple(range(256)))
    if done.returncode != 0:
        raise SubtreePushError("git_remote_unavailable", (done.stderr or "").strip()[-300:])
    default = None
    tips: dict = {}
    for line in done.stdout.splitlines():
        m = re.fullmatch(r"ref: refs/heads/(\S+)\s+HEAD", line)
        if m:
            default = m.group(1)
            continue
        m = re.fullmatch(r"([0-9a-f]{40}|[0-9a-f]{64})\s+(\S+)", line)
        if m:
            tips[m.group(2)] = m.group(1)
    return default, tips


def _subtree_fetch_compare_tip(cwd: Any, remote: str, ref: str, tip: str, *, run: Callable[..., Any],
                               timeout: float) -> bool:
    """Bring the child's compare tip into the object store before splitting.

    After a ``git subtree add/pull --squash`` join, ``git subtree split`` needs the commit
    named by ``git-subtree-split:`` or it fails with "could not rev-parse split hash".
    Object-only: an explicit refspec without destination, ``--no-write-fetch-head`` and
    ``--no-tags``, so no local ref or FETCH_HEAD changes. Returns whether a fetch ran.
    """
    if _subtree_commit_exists(cwd, tip, run=run, timeout=timeout):
        return False
    _subtree_git(cwd, ["fetch", "-q", "--no-write-fetch-head", "--no-tags", "--", remote, ref], run=run,
                 timeout=timeout)
    if not _subtree_commit_exists(cwd, tip, run=run, timeout=timeout):
        raise SubtreePushError("compare_tip_unavailable", f"{ref} {tip[:12]} not fetched")
    return True


def _subtree_push_guards(cwd: str, split_sha: str, compare: dict | None, *, child_only_paths: tuple[str, ...] = (),
                         run: Callable[..., Any], timeout: float) -> dict:
    """Refuse when the remote compare tip is not an ancestor of split_sha or has paths the split lacks.

    Same decisions as gh_identity#77 ``_subtree_push_guards``; ``child_only_paths`` (fnmatch,
    default empty, i.e. identical to #77) can explicitly allow known child-only files.
    """
    if not compare:
        raise SubtreePushError("compare_ref_unavailable", "remote comparison ref is required for ancestor validation")
    ref, tip = compare["ref"], compare["sha"]
    if not _subtree_is_ancestor(cwd, tip, split_sha, run=run, timeout=timeout):
        raise ValueError(f"remote ref {ref} is not an ancestor of the split commit")
    split_tree = _subtree_oid(cwd, f"{split_sha}^{{tree}}", run=run, timeout=timeout)
    child_tree = _subtree_oid(cwd, f"{tip}^{{tree}}", run=run, timeout=timeout)
    if not split_tree or not child_tree:
        raise ValueError(f"could not resolve trees for push guard (remote ref {ref})")
    only_child = sorted(_subtree_tree_paths(cwd, child_tree, run=run, timeout=timeout)
                        - _subtree_tree_paths(cwd, split_tree, run=run, timeout=timeout))
    allowed = [p for p in only_child if any(fnmatch.fnmatchcase(p, g) for g in child_only_paths)]
    refused = [p for p in only_child if p not in allowed]
    if refused:
        raise ValueError("child-only paths not present in split: " + ", ".join(refused)
                         + f" (remote ref {ref})")
    return {"compare_ref": ref, "compare_sha": tip, "child_only_paths": allowed}


def _subtree_push_split_tree_gate(cwd: Any, source: str, prefix: str, split_sha: str | None, *,
                                  run: Callable[..., Any], timeout: float) -> dict:
    """Re-validate the split tree immediately before ``git push`` of that commit."""
    check = _subtree_split_matches_prefix_tree(cwd, source, prefix, split_sha or "", run=run, timeout=timeout)
    if not split_sha or not check["trees_match"]:
        raise SubtreePushError("stale_subtree_split")
    return check


def subtree_push_plan(prefix: str, remote: str, branch: str, *, source: str = "HEAD",
                      compare_ref: str | None = None, child_only_paths: tuple[str, ...] = (), cwd: str = ".",
                      run: Callable[..., Any] = subprocess.run, timeout: float = 600.0) -> dict:
    """Read-only plan for pushing the verified split of ``prefix`` to a NEW ``branch`` on ``remote``.

    Order: validate input; HEAD/source and prefix tree; git subtree available;
    ``ls-remote`` (remote default, target branch, compare refs); refuse main/master, the
    remote default branch and an existing branch; fetch the compare tip object-only;
    tree-validated split; ancestor + child-only path guards. Local refs never move.
    """
    prefix = _subtree_prefix(prefix)
    remote = _subtree_remote(remote)
    branch = _subtree_branch(branch)
    source = _subtree_revision(source)
    if compare_ref is not None:
        compare_ref = _subtree_branch(compare_ref)
    patterns = tuple(child_only_paths or ())
    if any(not isinstance(p, str) or not p or "\0" in p for p in patterns):
        raise ValueError("invalid child-only path pattern")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0 or timeout == float("inf"):
        raise ValueError("timeout must be positive and finite")
    if branch in _SUBTREE_PROTECTED_BRANCHES:
        raise ValueError("refusing to push to main/master; use a new branch")
    source_sha = _subtree_oid(cwd, f"{source}^{{commit}}", run=run, timeout=timeout)
    if not source_sha:
        raise ValueError("revision not found")
    kind = _subtree_git(cwd, ["cat-file", "-t", f"{source_sha}:{prefix}"], run=run, timeout=timeout, ok=(0, 1, 128))
    if kind.returncode != 0 or kind.stdout.strip() != "tree":
        raise ValueError("prefix not found at HEAD")
    if not _subtree_available(cwd, run=run, timeout=timeout):
        raise SubtreePushError("subtree_unavailable")

    candidates = [compare_ref] if compare_ref is not None else list(_SUBTREE_COMPARE_FALLBACKS)
    refs = [f"refs/heads/{branch}"] + [f"refs/heads/{c}" for c in candidates if c != branch]
    default, tips = _subtree_ls_remote(cwd, remote, refs, run=run, timeout=timeout)
    if compare_ref is None and default and default != branch and default not in candidates:
        _, extra = _subtree_ls_remote(cwd, remote, [f"refs/heads/{default}"], run=run, timeout=timeout)
        tips.update(extra)
    if compare_ref is None and default:
        candidates = list(dict.fromkeys([default] + candidates))
    if branch == default:
        raise ValueError(f"refusing to push to the remote default branch {branch}; use a new branch")
    if tips.get(f"refs/heads/{branch}") is not None:
        raise ValueError(f"remote branch refs/heads/{branch} already exists; push creates a new branch only")

    if compare_ref is not None and not tips.get(f"refs/heads/{compare_ref}"):
        raise SubtreePushError("compare_ref_not_found", f"remote ref {compare_ref} does not exist")

    compare = None
    for candidate in candidates:
        tip = tips.get(f"refs/heads/{candidate}")
        if tip:
            compare = {"ref": candidate, "sha": tip}
            break
    if compare is None:
        raise SubtreePushError("compare_ref_unavailable",
                               "could not resolve remote comparison ref; specify an existing --compare-ref")
    fetched = _subtree_fetch_compare_tip(cwd, remote, f"refs/heads/{compare['ref']}", compare["sha"],
                                         run=run, timeout=timeout)

    split = subtree_split_verified(prefix, source=source_sha, cwd=cwd, run=run, timeout=timeout)
    if split["reason"]:
        raise SubtreePushError(split["reason"], split.get("detail", ""))
    guard = _subtree_push_guards(cwd, split["sha"], compare, child_only_paths=patterns, run=run, timeout=timeout)
    refspec = f"{split['sha']}:refs/heads/{branch}"
    return {"ok": True, "state": "planned", "write": False, "prefix": prefix, "remote": remote, "branch": branch,
            "source": source_sha, "remote_default_branch": default, "split_sha": split["sha"],
            "split_method": split["method"], "split_warning": split["warning"],
            "compare_tip_fetched": fetched, "push_guard": guard,
            "command": ["git", "push", f"--force-with-lease=refs/heads/{branch}:", "--", remote, refspec],
            "provenance": {"trailer": f"{_SUBTREE_TRAILER}: {source_sha} {split['sha']} {prefix}",
                           "monorepo_sha": source_sha, "split_sha": split["sha"]}}


def subtree_push(prefix: str, remote: str, branch: str, *, source: str = "HEAD", compare_ref: str | None = None,
                 child_only_paths: tuple[str, ...] = (), write: bool = False, cwd: str = ".",
                 run: Callable[..., Any] = subprocess.run, sleep: Callable[[float], None] = time.sleep,
                 delays: tuple[float, ...] = _GIT_COMMIT_PUSH_DELAYS, timeout: float = 600.0) -> dict:
    """Plan (default) or push the verified split SHA as ``<sha>:refs/heads/<branch>``.

    No ``--force``, no ``+`` refspec, never ``git subtree push``; the only lease is
    ``--force-with-lease=refs/heads/<branch>:`` (the branch must still not exist). With ``write`` the split
    tree is re-checked once right before the push, the push reuses the git-commit retry
    loop (network errors only), and the remote branch must then equal the split SHA.
    """
    if type(write) is not bool:
        raise ValueError("write must be a boolean")
    plan = subtree_push_plan(prefix, remote, branch, source=source, compare_ref=compare_ref,
                             child_only_paths=child_only_paths, cwd=cwd, run=run, timeout=timeout)
    if not write:
        return {**plan, "dry_run": True}
    split_sha, remote, branch = plan["split_sha"], plan["remote"], plan["branch"]
    gate = _subtree_push_split_tree_gate(cwd, plan["source"], plan["prefix"], split_sha, run=run, timeout=timeout)
    plan = {**plan, "write": True, "prefix_tree": gate["prefix_tree"], "split_tree": gate["split_tree"]}
    # Empty lease value: the push is accepted only while refs/heads/<branch> does not exist on
    # the remote, so a branch created after the ls-remote check (even at an ancestor of the
    # split, which a plain push would fast-forward) makes the push fail instead.
    target = f"refs/heads/{branch}"
    ok, output = git_commit_push_with_retry(cwd=cwd, run=run, sleep=sleep, delays=delays,
                                            push_args=(f"--force-with-lease={target}:", "--", remote,
                                                       f"{split_sha}:{target}"))
    if not ok:
        raise SubtreePushError("subtree_push_failed", f"{target}: " + output.strip()[-300:])
    _, tips = _subtree_ls_remote(cwd, remote, [f"refs/heads/{branch}"], run=run, timeout=timeout)
    pushed = tips.get(f"refs/heads/{branch}")
    if pushed != split_sha:
        raise SubtreePushError("post_write_verification_failed",
                               f"remote refs/heads/{branch} is {pushed or 'missing'}, expected {split_sha}")
    return {**plan, "state": "pushed", "verified": {"remote_branch_sha": pushed, "ok": True}}


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
    if command == "git-commit":
        if result.get("transcript"):
            return result["transcript"].rstrip("\n")
        if result.get("dry_run"):
            return f"dry run git-commit: would stage {len(result.get('files', []))} file(s)"
        reason = result.get("reason") or ""
        return f"{'OK' if result['ok'] else 'NG'} git-commit" + (f": {reason}" if reason else "")
    if command == "subtree-push":
        guard = result["push_guard"]
        compare = f" compare={guard['compare_ref']}@{guard['compare_sha'][:12]}"
        head = (f"OK subtree-push pushed {result['split_sha']} -> {result['remote']} {result['branch']}"
                if result.get("state") == "pushed" else
                f"dry run subtree-push: would run {' '.join(result['command'])}")
        return head + f"\nsplit={result['split_method']}{compare}\n{result['provenance']['trailer']}"
    if command == "commit-files":
        head = (f"{'OK' if result['ok'] else 'NG'} commit-files {result['repository']}@{result['branch']} "
                f"({'dry run' if result.get('dry_run') else 'applied'}) head {(result.get('head') or '?')[:7]}"
                + (f" -> {result['commit_sha'][:7]}" if result.get("commit_sha") else "")
                + (f": {result['reason']}" if result.get("reason") else ""))
        rows = [f"  {item['change']:9} {item['path']}"
                + ("" if item.get("delete") else f" ({item['bytes']} bytes, blob {item['blob_sha'][:7]})")
                for item in result.get("plan") or []]
        return "\n".join([head, *rows])
    if command == "pr-retarget":
        extra = f" ({result['warning']})" if result.get("warning") else ""
        return (f"{result['status']} {result['repository']}#{result['number']} "
                f"base {_base_ref_label(result.get('from'))} -> {result['to']}{extra}")
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
    p.add_argument("--ignore", default=r"(?i)copilot", metavar="REGEX",
                   help="drop check names matching REGEX before judging (default (?i)copilot; '' keeps all)")
    p.add_argument("--title")
    p.add_argument("--message-file")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("git-commit", help="git add FILE... and commit from --message-file (needs --write)")
    p.add_argument("--message-file", required=True, metavar="FILE", help="path to the commit message")
    p.add_argument("files", nargs="+", metavar="FILE", help="paths to stage and commit")
    p.add_argument("--trailer", action="append", default=[], metavar="KEY: VALUE",
                   help="trailer line to append if not already present (repeatable)")
    p.add_argument("--allow-staged", action="store_true",
                   help="allow files other than FILE... to already be staged")
    p.add_argument("--push", action="store_true", help="git push origin HEAD after committing (needs --write)")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("subtree-push", help="push the tree-verified subtree split of --prefix to a NEW remote "
                       "branch as <sha>:refs/heads/<branch> (planned unless --write)")
    p.add_argument("--root", default=".", help="monorepo checkout (default: .)")
    p.add_argument("--prefix", required=True); p.add_argument("--remote", required=True)
    p.add_argument("--branch", required=True, help="new branch on the remote; must not exist yet")
    p.add_argument("--source", default="HEAD", help="monorepo revision to split (default: HEAD)")
    p.add_argument("--compare-ref", help="remote branch that must be an ancestor of the split (default: remote HEAD)")
    p.add_argument("--child-only-path", action="append", default=[], metavar="GLOB",
                   help="allow a path that exists only in the child (fnmatch; repeatable; default: none)")
    p.add_argument("--timeout", type=float, default=600.0, help="seconds per git command (default: 600)")
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

    p = sub.add_parser("pr-retarget", help="change a stacked PR's base (planned unless --write)")
    p.add_argument("repo"); p.add_argument("number", type=int)
    p.add_argument("--base", default="main", help="new base branch name (default: main)")
    p.add_argument("--allow-unmerged-commits", action="store_true",
                   help="allow --write when the old base has commits not in the new base (ahead_by > 0)")
    p.add_argument("--allow-compare-failure", action="store_true",
                   help="allow --write when compare with the old base failed (for example branch deleted)")
    p.add_argument("--expect-head-sha", metavar="SHA",
                   help="optional: refuse --write unless the PR head still matches this SHA before PATCH")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("file-put", help="create or update one file via the contents API (needs --write)")
    p.add_argument("repo"); p.add_argument("path", help="path of the file inside the repository")
    p.add_argument("--from", dest="from_path", required=True, help="local file to read the new content from")
    p.add_argument("--branch", required=True)
    p.add_argument("--message", required=True, help="commit message")
    p.add_argument("--allow-default-branch", action="store_true",
                   help="permit writing directly to the repository's default branch")
    p.add_argument("--write", action="store_true")

    p = sub.add_parser("commit-files", help="ONE commit of several files via the Git Data API, no clone "
                                             "(planned unless --write; fast-forward only; verified)")
    p.add_argument("repo"); p.add_argument("--branch", required=True)
    p.add_argument("--file", action="append", default=[], metavar="PATH[=LOCAL]",
                   help="repository path, optionally =local file to read (default: the same path)")
    p.add_argument("--delete", action="append", default=[], metavar="PATH")
    p.add_argument("--message", required=True, help="commit message")
    p.add_argument("--expect-head", metavar="SHA", help="refuse unless the branch tip is exactly this full 40-hex SHA")
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
                        title=args.title, message=message, ignore=args.ignore, write=args.write, client=client)
    if command == "git-commit":
        if args.push and not args.write:
            raise GhOpsError("--push requires --write")
        return git_commit(args.message_file, list(args.files), trailers=list(args.trailer),
                          allow_staged=args.allow_staged, push=args.push, write=args.write)
    if command == "subtree-push":
        return subtree_push(args.prefix, args.remote, args.branch, source=args.source, compare_ref=args.compare_ref,
                            child_only_paths=tuple(args.child_only_path), write=args.write, cwd=args.root,
                            timeout=args.timeout)
    if command == "sync-main":
        return sync_main(base=args.base, remote=args.remote, write=args.write, push=args.push)
    if command == "pr-body-set":
        return pr_body_set(args.repo, args.number, new=_read_text(args.file), write=args.write, client=client)
    if command == "pr-edit":
        return pr_edit(args.repo, args.number, title=args.title, base=args.base, state=args.state,
                       write=args.write, client=client)
    if command == "pr-retarget":
        return pr_retarget(args.repo, args.number, args.base, write=args.write,
                           allow_unmerged_commits=args.allow_unmerged_commits,
                           allow_compare_failure=args.allow_compare_failure,
                           expect_head_sha=args.expect_head_sha, client=client)
    if command == "commit-files":
        files = {}
        for spec in args.file:
            path, _, local = spec.partition("=")
            files[path] = Path(local or path).read_bytes()
        return commit_files(args.repo, args.branch, files, args.message, deletes=tuple(args.delete),
                            expect_head=args.expect_head, write=args.write,
                            allow_default_branch=args.allow_default_branch, client=client)
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
