#!/usr/bin/env python3
"""Read-only GitHub CI status index. Python stdlib only."""
import argparse
import datetime
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OWNER = "myon-bioinformatics"

def get(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ci-status/0.1",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)

def repository(name):
    if not name or "/" in name and name.count("/") != 1:
        raise ValueError("invalid repository")
    parts = (name if "/" in name else OWNER + "/" + name).split("/")
    if not all(p and all(c.isalnum() or c in "-_." for c in p) for p in parts):
        raise ValueError("invalid repository")
    return "/".join(parts)

def observe(name, pr=None):
    name = repository(name)
    base = "/repos/" + name
    if pr is not None:
        pull = get(base + "/pulls/" + str(pr))
        sha = pull["head"]["sha"]
        target = "PR #" + str(pr)
    else:
        meta = get(base)
        ref = meta["default_branch"]
        sha = get(base + "/git/ref/heads/" + urllib.parse.quote(ref, safe=""))["object"]["sha"]
        target = ref
    checks = []
    page = 1
    while True:
        data = get(base + "/commits/" + sha + "/check-runs?per_page=100&page=" + str(page))
        batch = data["check_runs"]
        checks.extend(batch)
        if len(checks) >= data["total_count"] or not batch:
            break
        page += 1
        if page > 20:
            raise RuntimeError("check pagination incomplete")
    rows = [{"id": x["id"], "name": x["name"], "status": x["status"],
             "conclusion": x.get("conclusion"), "url": x.get("html_url")}
            for x in checks]
    pending = sum(x["status"] != "completed" for x in rows)
    failed = sum(x["conclusion"] in ("failure", "timed_out", "action_required", "startup_failure") for x in rows)
    state = "FAILED" if failed else "RUNNING" if pending else "NO CHECKS" if not rows else "COMPLETE"
    return {"repository": name, "target": target, "head_sha": sha, "state": state,
            "completed": len(rows) - pending, "total": len(rows), "checks": rows}

def render(rows):
    for item in rows:
        print(f'{item["repository"]}  {item["target"]}  {item["state"]}  {item["completed"]}/{item["total"]}  {item["head_sha"][:8]}')
        for check in item["checks"]:
            print(f'  {check["status"]:10} {str(check["conclusion"] or "-"):12} {check["name"]}')
            
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repository", nargs="?")
    p.add_argument("--pr", type=int)
    p.add_argument("--refresh", action="store_true", help="Force fresh API observation (default already fresh)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--repos", default="Ironmate,flutter_navigation_basic,markdown,ascii_artist",
                   help="Comma-separated repositories for the all-repositories view")
    a = p.parse_args(argv)
    if a.pr is not None and (a.pr < 1 or not a.repository):
        p.error("--pr requires a repository and positive PR number")
    names = [a.repository] if a.repository else [x.strip() for x in a.repos.split(",") if x.strip()]
    rows = []
    errors = []
    for name in names:
        try:
            rows.append(observe(name, a.pr))
        except (ValueError, KeyError, RuntimeError, urllib.error.URLError, OSError) as exc:
            errors.append({"repository": name, "error": type(exc).__name__})
    output = {"schema": "ci-observation/1",
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "repositories": rows, "errors": errors}
    if a.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        render(rows)
        for error in errors:
            print(f'{error["repository"]}  UNKNOWN ({error["error"]})')
    return 2 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
