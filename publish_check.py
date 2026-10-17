#!/usr/bin/env python3
"""Check that a merge actually reached a static site, from several angles, one line each.

Canonical parent tool migrated from browser-test-kit ``scripts/publish_check.py``
(61872b2). Consumers vendor this repository directly; do not copy from btk.

Replaces ``sleep 60; curl -s -o /dev/null -w '%{http_code}' URL``, which only says "200"
(an old copy is also 200) and says nothing useful when the network is blocked:

* ``deploy``  (``--repo OWNER/REPO``): the newest ``github-pages`` deployment's commit and
  state via the GitHub API (``gh api`` if available, else ``GITHUB_TOKEN``/``GH_TOKEN``);
  ok when it is ``success`` and its commit starts with ``--commit`` (default: local HEAD);
* ``http``  per URL: status code, with a cache-busting query (``--no-bust`` to disable);
* ``bytes`` when ``--local-root`` has the same path: sha256 of served vs local bytes, so a
  stale CDN copy shows as ``stale`` instead of passing as 200;
* ``text``  for each ``--contains`` string: present in the body.

``--wait SECONDS`` repeats until every check is ok (``--interval``), so it also replaces the
blind ``sleep``. PATHs are joined to ``--site``; full URLs are used as given. Stdlib only;
runs under ``python -S``::

    python -S publish_check.py --site https://OWNER.github.io/ \\
        tools/lab/pixiv-panel.js tools/lab/index.html --local-root . \\
        --contains 'pixiv-panel.js' --repo OWNER/OWNER.github.io --wait 600

Exit codes: 0 = all checks ok; 1 = a check failed, is stale, or deployment evidence is
unavailable; 3 = at least one HTTP target is unreachable (DNS/proxy) and all other
checks are ok. Check each independent evidence line; deploy alone cannot prove delivery.
2 = usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def fetch(url: str, timeout: float = 20.0) -> tuple[int | None, bytes, str]:
    """(status, body, error). status None means unreachable (DNS, proxy refusal, timeout)."""
    request = urllib.request.Request(url, headers={"User-Agent": "publish-check", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b"", ""
    except (urllib.error.URLError, OSError) as exc:
        return None, b"", str(getattr(exc, "reason", exc))[:120]


def github_json(path: str) -> object:
    if shutil.which("gh"):
        done = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
        if done.returncode:
            raise RuntimeError(done.stderr.strip()[:200])
        return json.loads(done.stdout)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "publish-check"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com/" + path.lstrip("/"), headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def deploy_check(repo: str, commit: str | None, api=github_json) -> tuple[str, str]:
    """Newest github-pages deployment: (state, line)."""
    try:
        deployments = api(f"repos/{repo}/deployments?environment=github-pages&per_page=1")
        if not isinstance(deployments, list):
            raise ValueError("invalid deployments response: expected a list")
        if not deployments:
            return "fail", "fail deploy: no github-pages deployment"
        dep = deployments[0]
        if not isinstance(dep, dict) or not isinstance(dep.get("id"), int) or not isinstance(dep.get("sha"), str) or not dep["sha"]:
            raise ValueError("invalid deployment: expected id and sha")
        statuses = api(f"repos/{repo}/deployments/{dep['id']}/statuses?per_page=1")
        if not isinstance(statuses, list):
            raise ValueError("invalid statuses response: expected a list")
        if statuses and (not isinstance(statuses[0], dict) or statuses[0].get("state") not in
                         ("error", "failure", "inactive", "pending", "queued", "in_progress", "success")):
            raise ValueError("invalid deployment status")
        state = statuses[0]["state"] if statuses else "pending"
        sha = dep["sha"]
    except Exception as exc:  # noqa: BLE001 - any API failure is reported, not raised
        return "deploy-unavailable", f"deploy-unavailable deploy: {str(exc)[:120]}"
    if commit and not sha.startswith(commit):
        return "stale", f"stale deploy: newest {sha[:7]} {state}, want {commit[:7]}"
    if state != "success":
        return ("fail" if state in ("failure", "error") else "stale"), f"{'fail' if state in ('failure', 'error') else 'stale'} deploy: {sha[:7]} {state}"
    return "ok", f"ok deploy: {sha[:7]} success"


def url_checks(url: str, local: str | None, contains: list[str], bust: bool, get=fetch) -> list[tuple[str, str]]:
    target = url
    if bust:
        target += ("&" if "?" in url else "?") + "publish_check=" + str(int(time.time()))
    status, body, error = get(target)
    if status is None:
        return [("unreachable", f"unreachable http: {url} ({error})")]
    out = [("ok" if 200 <= status < 300 else "fail", f"{'ok' if 200 <= status < 300 else 'fail'} http {status}: {url}")]
    if status >= 300:
        return out
    if local is not None:
        with open(local, "rb") as handle:
            want = hashlib.sha256(handle.read()).hexdigest()
        got = hashlib.sha256(body).hexdigest()
        out.append(("ok", f"ok bytes: {url} = {local}") if got == want else
                   ("stale", f"stale bytes: {url} sha256 {got[:12]} != local {want[:12]} ({len(body)} bytes served)"))
    text = body.decode("utf-8", "replace")
    for needle in contains:
        out.append(("ok", f"ok text: {needle!r} in {url}") if needle in text else
                   ("fail", f"fail text: {needle!r} not in {url}"))
    return out


def run_once(args, get=fetch, api=github_json) -> list[tuple[str, str]]:
    results = []
    if args.repo:
        commit = args.commit
        if commit is None:
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
            commit = head.stdout.strip() or None
        results.append(deploy_check(args.repo, commit, api))
    for item in args.targets:
        url = item if "://" in item else urllib.parse.urljoin(args.site or "", item.lstrip("/"))
        local = None
        if args.local_root and "://" not in item:
            candidate = os.path.join(args.local_root, urllib.parse.unquote(item.lstrip("/").split("?")[0]))
            if candidate.endswith("/") or os.path.isdir(candidate):
                candidate = os.path.join(candidate, "index.html")
            local = candidate if os.path.isfile(candidate) else None
        contains = args.contains if (not args.contains_on or item in args.contains_on) else []
        results.extend(url_checks(url, local, contains, not args.no_bust, get))
    return results


def verdict(results: list[tuple[str, str]]) -> int:
    states = {state for state, _ in results}
    if states <= {"ok"}:
        return 0
    if states <= {"ok", "unreachable"} and any(s == "unreachable" for s, _ in results):
        return 3
    return 1


def main(argv: list[str] | None = None, get=fetch, api=github_json, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("targets", nargs="*", help="paths under --site, or full URLs")
    parser.add_argument("--site", help="base URL, e.g. https://OWNER.github.io/")
    parser.add_argument("--local-root", help="checkout whose files the site serves (enables the bytes check)")
    parser.add_argument("--contains", action="append", default=[], help="text that must appear (repeatable)")
    parser.add_argument("--contains-on", action="append", help="limit --contains to these targets")
    parser.add_argument("--repo", help="OWNER/REPO: also check the newest github-pages deployment")
    parser.add_argument("--commit", help="deployment commit prefix to expect (default: local HEAD)")
    parser.add_argument("--wait", type=float, default=0, help="retry for up to this many seconds")
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--no-bust", action="store_true", help="do not add a cache-busting query")
    args = parser.parse_args(argv)
    if not args.targets and not args.repo:
        parser.error("give at least one target or --repo")
    if any("://" not in t for t in args.targets) and not args.site:
        parser.error("relative targets need --site")
    deadline = time.monotonic() + args.wait
    attempt = 0
    while True:
        attempt += 1
        results = run_once(args, get, api)
        code = verdict(results)
        if code in (0, 3) or time.monotonic() + args.interval > deadline:
            break
        sleep(args.interval)
    for _state, line in results:
        print(line)
    print(f"attempts={attempt} verdict={['ok', 'not-yet-or-failed', '', 'unreachable'][code]}")
    return code


if __name__ == "__main__":
    sys.exit(main())
