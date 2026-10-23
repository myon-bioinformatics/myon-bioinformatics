#!/usr/bin/env python3
"""Read-only GitHub CI status index. Python stdlib only.

Exit codes: 0 = observation succeeded (with --ci-check, all states are COMPLETE or
NO CHECKS); 1 = --ci-check observed a non-passing state; 2 = observation or usage error.
Observation errors take precedence over --ci-check failures. --json uses the same codes.
Both output formats include the UTC observation timestamp. Exception text is omitted
unless ``--explain`` is set; that flag adds detail (token values removed) and hint.
"""
import argparse
import datetime
import json
import os
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

def _detail(exc):
    """Exception text for callers, with token values removed."""
    text = str(exc).replace("\n", " ")
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "***")
    return text[:500]


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
    p.add_argument("--explain", action="store_true",
                   help="include detail and hint on observation errors; default omits exception text")
    p.add_argument("--ci-check", action="store_true", help="Exit with 1 if any observed repository state is not COMPLETE or NO CHECKS")
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
            item = {"repository": name, "error": type(exc).__name__}
            if a.explain:
                item["detail"] = _detail(exc)
                item["hint"] = ("Observation failed before a CI state was known. Exit 2 wins over --ci-check. "
                                "detail is the exception text with token values removed.")
            errors.append(item)
    output = {"schema": "ci-observation/1",
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "repositories": rows, "errors": errors}
    if a.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f'Observed at: {output["observed_at"]}')
        render(rows)
        for error in errors:
            print(f'{error["repository"]}  UNKNOWN ({error["error"]})')
            if a.explain:
                print(f'  detail: {error["detail"]}')
                print(f'  hint: {error["hint"]}')
    if errors:
        return 2
    if a.ci_check and any(r["state"] not in ("COMPLETE", "NO CHECKS") for r in rows):
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
