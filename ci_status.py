#!/usr/bin/env python3
"""Read-only GitHub CI status index. Python stdlib only."""
import argparse
import datetime
import json
import urllib.error
from urllib.parse import quote
import importlib.util
from pathlib import Path


def _load_ghi():
    source = Path(__file__).resolve().parent / "vendor" / "gh_identity.py"
    if not source.is_file():
        raise RuntimeError("canonical gh_identity.py is not present")
    spec = importlib.util.spec_from_file_location("ci_status_ghi", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    ghi = _load_ghi()
except RuntimeError:
    ghi = None

OWNER = "myon-bioinformatics"
__version__ = "0.2.0"
__all__ = ["repository", "observe", "render", "main"]

def get(path):
    """Compatibility shim backed by GHI transport; no independent HTTP client."""
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    return ghi.request("GET", path.lstrip("/"))

def repository(name):
    if not name or "/" in name and name.count("/") != 1:
        raise ValueError("invalid repository")
    parts = (name if "/" in name else OWNER + "/" + name).split("/")
    if not all(p not in ("", ".", "..") and all(c.isascii() and (c.isalnum() or c in "-_.") for c in p) for p in parts):
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
        sha = get(base + "/git/ref/heads/" + quote(ref, safe=""))["object"]["sha"]
        target = ref
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    observation = ghi.checks_for_sha(name, sha)
    if not observation["complete"]:
        raise RuntimeError("check pagination incomplete")
    checks = observation["checks"]
    rows = [{"id": x["id"], "name": x["name"], "status": x["status"],
             "conclusion": x.get("conclusion"), "url": x.get("url")}
            for x in checks]
    pending = sum(x["status"] != "completed" for x in rows)
    failed = sum(x["conclusion"] in ("failure", "timed_out", "action_required", "startup_failure") for x in rows)
    cancelled = sum(x["conclusion"] in ("cancelled", "stale") for x in rows)
    incomplete = sum(x["status"] == "completed" and x["conclusion"] in (None, "skipped", "neutral") for x in rows)
    state = "FAILED" if failed else "RUNNING" if pending else "INCOMPLETE" if cancelled or incomplete else "NO CHECKS" if not rows else "COMPLETE"
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
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
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
