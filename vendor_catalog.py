"""Read-only recommended baselines; consumer locks remain independent."""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import keyword
from pathlib import Path
import re
import sys
import warnings

import vendor_sync

SCHEMA = "vendor-catalog/1"
# Consumer-held catalog snapshots (for example Ironmate) may omit source until this date.
LEGACY_IMPLICIT_SOURCE_DEADLINE = datetime.date(2027, 4, 1)


def legacy_implicit_source_allowed(allow_flag: bool) -> bool:
    if datetime.date.today() > LEGACY_IMPLICIT_SOURCE_DEADLINE:
        return False
    return allow_flag


def normalized_module_name(repo_name: str) -> str:
    """Lowercase repository name and map '-' and '.' to '_' for module identity."""
    module = repo_name.casefold().replace("-", "_").replace(".", "_")
    if not module.isidentifier() or keyword.iskeyword(module):
        raise ValueError(f"repository name does not normalize to a valid Python identifier: {repo_name}")
    return module


def _legacy_implicit_source(repo: str) -> str:
    return repo.split("/")[1] + ".py"


def _validate_source(source) -> str:
    if not isinstance(source, str) or not source or source != source.strip():
        raise ValueError("source required")
    vendor_sync._path(source)
    return source


def validate_pin_commits(normalized_catalog):
    """Require every distinct catalog pin to resolve on the public GitHub remote."""
    seen: set[tuple[str, str]] = set()
    for tool in normalized_catalog["tools"]:
        key = (tool["repository"].casefold(), tool["commit"])
        if key in seen:
            continue
        seen.add(key)
        entry = "{}@{}".format(tool["source"], tool["commit"][:7])
        vendor_sync.public_commit_exists(tool["repository"], tool["commit"], entry_label=entry)
    return normalized_catalog


def validate(catalog, *, allow_legacy_implicit_source: bool = False, verify_pins: bool = False):
    """Validate and return a normalized copy, never modifying caller data."""
    if not isinstance(catalog, dict) or set(catalog) != {"schema", "tools"} or catalog["schema"] != SCHEMA:
        raise ValueError("expected vendor-catalog/1")
    if not isinstance(catalog["tools"], list) or not catalog["tools"]:
        raise ValueError("tools must be a nonempty list")
    legacy_ok = legacy_implicit_source_allowed(allow_legacy_implicit_source)
    result = copy.deepcopy(catalog)
    seen = set()
    normalized_repos: dict[str, str] = {}
    for tool in result["tools"]:
        required = {"repository", "commit", "reason"}
        optional = {"consumers", "default_enrollment", "source"}
        if not isinstance(tool, dict) or not required <= tool.keys() or tool.keys() - required - optional:
            raise ValueError("invalid tool fields")
        repo = tool["repository"]
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(p in (".", "..") for p in repo.split("/")):
            raise ValueError("expected owner/repository")
        repo_name = repo.split("/")[1]
        module = normalized_module_name(repo_name)
        repo_key = repo.casefold()
        prior = normalized_repos.get(module)
        if prior is not None and prior != repo_key:
            raise ValueError("normalized repository module name collision")
        normalized_repos[module] = repo_key
        if "source" in tool:
            source = _validate_source(tool["source"])
        elif legacy_ok:
            source = _legacy_implicit_source(repo)
            tool["source"] = source
            warnings.warn(
                f"implicit source for {repo} is deprecated; set source explicitly "
                f"(required after {LEGACY_IMPLICIT_SOURCE_DEADLINE.isoformat()})",
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            raise ValueError("source required")
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
    if verify_pins:
        validate_pin_commits(result)
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
    parser.add_argument(
        "--allow-legacy-implicit-source",
        action="store_true",
        help="accept omitted source using the retired <repository>.py default (consumer snapshots until "
        f"{LEGACY_IMPLICIT_SOURCE_DEADLINE.isoformat()})",
    )
    args = parser.parse_args(argv)
    try:
        catalog = validate(
            json.loads(Path(args.catalog).read_text(encoding="utf-8")),
            allow_legacy_implicit_source=args.allow_legacy_implicit_source,
            verify_pins=(args.mode == "validate"),
        )
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
