"""Read-only, fail-closed reconciliation of consumer locks against the parent registry.

No lock writes, no compatibility claim, no automatic enrollment.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import vendor_catalog
import vendor_consumers
import vendor_fleet_plan
import vendor_sync

SCHEMA = "vendor-lock-reconciliation/1"


def reconcile(catalog, registry, locks, *, consumers):
    """Pure comparison of supplied locks; missing/malformed input is an error."""
    catalog = vendor_catalog.validate(catalog)
    registry = vendor_consumers.validate(registry)
    names = sorted(set(consumers), key=str.casefold)
    if not names:
        raise ValueError("at least one consumer required")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("duplicate consumer")
    outputs = []
    for name in names:
        consumer = vendor_consumers.get_consumer(registry, name)
        if consumer["state"] == "legacy":
            outputs.append({"consumer": consumer["repository"], "state": "legacy",
                            "legacy_provenance": consumer["legacy_provenance"],
                            "review_required": True})
            continue
        if name not in locks:
            raise ValueError("missing actual lock for " + name)
        lock = vendor_sync.validate(locks[name])
        # The validator may return a normalized value or just validate in place.
        lock = locks[name] if lock is None else lock
        topology = vendor_consumers.compare(registry, lock, name)
        baseline = vendor_catalog.compare(catalog, lock, name)
        fleet = vendor_fleet_plan.plan(catalog, registry, active_consumers=[name])
        candidates = [row for row in fleet["rows"] if row["action"] == "needs_registration"]
        skipped = [row for row in fleet["rows"] if row["action"] == "skipped"]
        mismatched = [row for row in baseline["tools"]
                      if row["enrollment"] == "enrolled"
                      and row["comparison"] == "different"]
        # Catalog's missing means absent pin, not proof of failed installation.
        outputs.append({
            "consumer": consumer["repository"], "state": "locked",
            "lock_path": consumer["lock"], "topology": topology,
            "unregistered_candidates": candidates, "explicit_skips": skipped,
            "baseline_mismatches": mismatched,
            "baseline_absent": [row for row in baseline["tools"]
                                if row["enrollment"] == "enrolled" and row["comparison"] == "missing"],
            "registration_review": "manual_dependency_license_ci_review_required",
            "compatibility": "unverified",
        })
    return {"schema": SCHEMA, "read_only": True, "compatibility": "unverified",
            "results": outputs}


def fetch_lock(repository, path, *, ref="main", token=None):
    """Fetch actual GitHub contents; errors must not become empty locks."""
    vendor_consumers._repository(repository)
    vendor_sync._path(path)
    url = ("https://api.github.com/repos/" + repository + "/contents/" +
           quote(path, safe="/") + "?ref=" + quote(ref, safe=""))
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "vendor-lock-reconciliation"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise ValueError("invalid GitHub content response: " + repository)
    raw = base64.b64decode(data["content"], validate=False)
    return json.loads(raw.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="vendor-catalog.json")
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--consumer", action="append", required=True)
    parser.add_argument("--lock", action="append", default=[], metavar="OWNER/REPO=FILE",
                        help="offline lock input; otherwise retrieve actual lock from GitHub")
    parser.add_argument("--ref", default="main", help="consumer revision (default: main)")
    parser.add_argument("--output", help="write stable JSON report, otherwise stdout")
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        validated = vendor_consumers.validate(registry)
        local = {}
        for value in args.lock:
            name, sep, filename = value.partition("=")
            if not sep or not name or not filename or name in local:
                raise ValueError("expected unique --lock OWNER/REPO=FILE")
            local[name] = json.loads(Path(filename).read_text(encoding="utf-8"))
        if set(local) - set(args.consumer):
            raise ValueError("--lock consumer was not selected")
        locks = {}
        for name in args.consumer:
            consumer = vendor_consumers.get_consumer(validated, name)
            if consumer["state"] == "locked":
                locks[name] = local[name] if name in local else fetch_lock(
                    name, consumer["lock"], ref=args.ref, token=os.environ.get("GH_TOKEN"))
        report = reconcile(catalog, validated, locks, consumers=args.consumer)
        encoded = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
        drift = any(r["state"] == "locked" and (
            not r["topology"]["matches"] or r["baseline_mismatches"])
            for r in report["results"])
        return 1 if args.fail_on_drift and drift else 0
    except (ValueError, KeyError, TypeError, OSError, HTTPError, URLError,
            json.JSONDecodeError) as exc:
        print("vendor lock reconciliation: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
