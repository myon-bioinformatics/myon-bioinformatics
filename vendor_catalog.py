"""Read-only recommended baselines; consumer locks remain independent."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

import vendor_sync

SCHEMA = "vendor-catalog/1"


def validate(catalog):
    """Validate and return a normalized copy, never modifying caller data."""
    if not isinstance(catalog, dict) or set(catalog) != {"schema", "tools"} or catalog["schema"] != SCHEMA:
        raise ValueError("expected vendor-catalog/1")
    if not isinstance(catalog["tools"], list) or not catalog["tools"]:
        raise ValueError("tools must be a nonempty list")
    result = copy.deepcopy(catalog)
    seen = set()
    for tool in result["tools"]:
        required = {"repository", "commit", "reason"}
        optional = {"source", "consumers", "default_enrollment"}
        if not isinstance(tool, dict) or not required <= tool.keys() or tool.keys() - required - optional:
            raise ValueError("invalid tool fields")
        repo = tool["repository"]
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(p in (".", "..") for p in repo.split("/")):
            raise ValueError("expected owner/repository")
        source = tool.setdefault("source", repo.split("/")[1] + ".py")
        vendor_sync._path(source)
        vendor_sync._hex(tool["commit"], 40)
        if not isinstance(tool["reason"], str) or not tool["reason"].strip():
            raise ValueError("recommendation reason required")
        key = (repo.casefold(), source)
        if key in seen:
            raise ValueError("duplicate repository/source")
        seen.add(key)
        consumers = tool.setdefault("consumers", {})
        if not isinstance(consumers, dict):
            raise ValueError("consumers must be an object")
        names = set()
        for consumer in consumers:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", consumer) or any(p in (".", "..") for p in consumer.split("/")) or consumer.casefold() in names:
                raise ValueError("invalid or duplicate consumer")
            names.add(consumer.casefold())
        decisions = list(consumers.values())
        if "default_enrollment" in tool:
            decisions.append(tool["default_enrollment"])
        for decision in decisions:
            if not isinstance(decision, dict) or set(decision) != {"enrollment", "reason"} or decision["enrollment"] not in ("enrolled", "skipped"):
                raise ValueError("expected enrolled or skipped with reason")
            if not isinstance(decision["reason"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", decision["reason"]):
                raise ValueError("expected machine-readable enrollment reason")
    return result


def compare(catalog, lock, consumer):
    """Offline pin comparison, not byte verification or compatibility evidence."""
    catalog = validate(catalog)
    vendor_sync.validate(lock)
    if not isinstance(consumer, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", consumer) or any(p in (".", "..") for p in consumer.split("/")):
        raise ValueError("expected consumer owner/repository")
    rows = []
    for tool in catalog["tools"]:
        decision = next((v for k, v in tool["consumers"].items() if k.casefold() == consumer.casefold()),
                        tool.get("default_enrollment", {"enrollment": "enrolled", "reason": "default_single_file_policy"}))
        locked = [copy.deepcopy(item) for item in lock["files"]
                  if item["repository"].casefold() == tool["repository"].casefold() and item["source"] == tool["source"]]
        status = "missing" if not locked else ("at_recommended" if all(i["commit"] == tool["commit"] for i in locked) else "different")
        rows.append({"repository": tool["repository"], "source": tool["source"],
                     "recommended_commit": tool["commit"], "locked": locked,
                     **decision, "comparison": status})
    return {"schema": "vendor-catalog-comparison/1", "consumer": consumer, "tools": rows}


def verify(catalog):
    """Retrieve exact recommendations through canonical vendor_sync verification."""
    catalog = validate(catalog)
    return {"schema": "vendor-catalog-verification/1", "files": [
        vendor_sync.inspect_source(tool["repository"], tool["commit"], tool["source"])
        for tool in catalog["tools"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "compare", "verify"))
    parser.add_argument("--catalog", default="vendor-catalog.json")
    parser.add_argument("--lock")
    parser.add_argument("--consumer")
    args = parser.parse_args(argv)
    try:
        catalog = validate(json.loads(Path(args.catalog).read_text(encoding="utf-8")))
        if args.mode == "compare":
            if not args.lock or not args.consumer:
                raise ValueError("compare requires --lock and --consumer")
            result = compare(catalog, json.loads(Path(args.lock).read_text(encoding="utf-8")), args.consumer)
        elif args.mode == "verify":
            result = verify(catalog)
        else:
            result = catalog
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
