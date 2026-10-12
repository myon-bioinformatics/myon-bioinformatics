"""Read-only, fail-closed reconciliation of consumer locks against the parent registry.

No lock writes, no compatibility claim, no automatic enrollment. Reports lock
topology, recommended pins and manually reviewed registration candidates
separately. Actual remote locks are fetched from immutable consumer commits.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
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
REVIEW_SCHEMA = "vendor-registration-review/1"
DISPOSITIONS = frozenset(("scoped_ci_trial_candidate", "further_investigation"))


def _review_index(evidence):
    if evidence is None:
        return {}
    if not isinstance(evidence, dict) or set(evidence) != {"schema", "purpose", "reviews"} or evidence["schema"] != REVIEW_SCHEMA:
        raise ValueError("expected vendor-registration-review/1")
    if not isinstance(evidence["reviews"], list):
        raise ValueError("reviews must be an array")
    result = {}
    for item in evidence["reviews"]:
        required = {"consumer", "consumer_revision", "tool_repository", "source",
                    "recommended_commit", "proposed_destination", "disposition", "reason",
                    "registration_approved", "source_examined", "source_dependencies",
                    "upstream_license", "license_source", "license_verified_at_recommended_commit",
                    "consumer_ci_workflow", "consumer_ci_with_candidate", "existing_destination_collision"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("invalid candidate review fields")
        for field in ("consumer", "tool_repository"):
            vendor_consumers._repository(item[field])
        for field in ("source", "proposed_destination", "license_source", "consumer_ci_workflow"):
            vendor_sync._path(item[field])
        for field in ("consumer_revision", "recommended_commit"):
            vendor_sync._hex(item[field], 40)
        if (item["disposition"] not in DISPOSITIONS
                or not isinstance(item["reason"], str) or not item["reason"]
                or item["registration_approved"] is not False
                or item["source_examined"] is not True
                or item["license_verified_at_recommended_commit"] is not True
                or item["consumer_ci_with_candidate"] != "not_run"
                or item["source_dependencies"] != "python_stdlib_only"
                or item["upstream_license"] != "MIT"
                or item["existing_destination_collision"] is not None
                and not isinstance(item["existing_destination_collision"], str)):
            raise ValueError("invalid review evidence or approval claim")
        key = (item["consumer"].casefold(), item["tool_repository"].casefold(), item["source"])
        if key in result:
            raise ValueError("duplicate candidate review")
        result[key] = item
    return result


def _candidate(row, consumer, review_index, metadata):
    result = dict(row)
    key = (consumer.casefold(), row["tool_repository"].casefold(), row["source"])
    evidence = review_index.get(key)
    revision = metadata.get("commit") if isinstance(metadata, dict) else None
    if evidence is None:
        result["review"] = {"evidence_status": "missing", "disposition": "further_investigation",
                            "registration_approved": False}
    elif (revision is None or evidence["consumer_revision"] != revision
          or evidence["recommended_commit"] != row["recommended_commit"]):
        result["review"] = {"evidence_status": "stale_or_unpinned",
                            "disposition": "further_investigation",
                            "registration_approved": False}
    else:
        # Only a scoped CI trial, NEVER an approved enrollment.
        result["review"] = {"evidence_status": "current", **evidence}
    return result


def reconcile(catalog, registry, locks, *, consumers, metadata=None, evidence=None):
    """Pure comparison; caller-supplied metadata is an immutable snapshot hint."""
    catalog = vendor_catalog.validate(catalog)
    registry = vendor_consumers.validate(registry)
    names = sorted(set(consumers), key=str.casefold)
    if not names:
        raise ValueError("at least one consumer required")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("duplicate consumer")
    meta = metadata or {}
    review_index = _review_index(evidence)
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
        topology = vendor_consumers.compare(registry, lock, name)
        baseline = vendor_catalog.compare(catalog, lock, name)
        fleet = vendor_fleet_plan.plan(catalog, registry, active_consumers=[name])
        source_meta = meta.get(name, {"resolution": "offline_unpinned"})
        candidates = [_candidate(row, name, review_index, source_meta)
                      for row in fleet["rows"] if row["action"] == "needs_registration"]
        skipped = [row for row in fleet["rows"] if row["action"] == "skipped"]
        mismatched = [row for row in baseline["tools"]
                      if row["enrollment"] == "enrolled"
                      and row["comparison"] == "different"]
        absent = [row for row in baseline["tools"]
                  if row["enrollment"] == "enrolled" and row["comparison"] == "missing"]
        counts = {
            "topology_missing": len(topology["missing"]),
            "topology_unexpected": len(topology["unexpected"]),
            "unregistered_candidates": len(candidates),
            "explicit_skips": len(skipped),
            "baseline_mismatches": len(mismatched),
            "baseline_absent": len(absent),
            "scoped_ci_trial_candidates": sum(
                row["review"]["evidence_status"] == "current"
                and row["review"]["disposition"] == "scoped_ci_trial_candidate"
                for row in candidates),
            "needs_investigation": sum(
                row["review"]["evidence_status"] != "current"
                or row["review"]["disposition"] == "further_investigation"
                for row in candidates),
        }
        outputs.append({
            "consumer": consumer["repository"], "state": "locked",
            "lock_path": consumer["lock"], "lock_provenance": source_meta,
            "lock_file_bytes_verified": False,
            "topology": topology,
            "unregistered_candidates": candidates, "explicit_skips": skipped,
            "baseline_mismatches": mismatched, "baseline_absent": absent,
            "counts": counts,
            "registration_review": "manual_dependency_license_ci_review_required",
            "compatibility": "unverified",
        })
    return {"schema": SCHEMA, "read_only": True, "compatibility": "unverified",
            "results": outputs}


def _json_api(url, token=None):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "vendor-lock-reconciliation"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=20) as response:
        raw = response.read(vendor_sync.MAX_BYTES + 1)
    if len(raw) > vendor_sync.MAX_BYTES:
        raise ValueError("GitHub response exceeds byte limit")
    return json.loads(raw.decode("utf-8"))


def fetch_revision(repository, ref, *, token=None):
    """Resolve moving main to an immutable full SHA before reading the lock."""
    vendor_consumers._repository(repository)
    if not isinstance(ref, str) or not ref or any(ord(c) < 32 for c in ref):
        raise ValueError("invalid consumer ref")
    url = ("https://api.github.com/repos/" + repository + "/commits/" +
           quote(ref, safe=""))
    record = _json_api(url, token)
    if not isinstance(record, dict):
        raise ValueError("invalid commit response")
    sha = record.get("sha")
    vendor_sync._hex(sha, 40)
    return sha


def fetch_lock(repository, path, *, ref, token=None):
    """Fetch manifest from a pinned commit, validate Git blob and raw-byte hash."""
    vendor_consumers._repository(repository)
    vendor_sync._path(path)
    vendor_sync._hex(ref, 40)
    url = ("https://api.github.com/repos/" + repository + "/contents/" +
           quote(path, safe="/") + "?ref=" + quote(ref, safe=""))
    data = _json_api(url, token)
    if (not isinstance(data, dict) or data.get("type") != "file"
            or data.get("encoding") != "base64"
            or not isinstance(data.get("content"), str)):
        raise ValueError("invalid GitHub content response: " + repository)
    raw = base64.b64decode("".join(data["content"].split()), validate=True)
    if len(raw) > vendor_sync.MAX_BYTES or vendor_sync.git_blob(raw) != data.get("sha"):
        raise ValueError("GitHub lock blob mismatch or byte limit exceeded")
    lock = json.loads(raw.decode("utf-8"))
    vendor_sync.validate(lock)
    return lock, {"resolution": "pinned_github_commit", "commit": ref,
                  "lock_blob_sha": data["sha"], "lock_sha256": hashlib.sha256(raw).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="vendor-catalog.json")
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--review", help="review evidence JSON; stale/unpinned reviews never approve enrollment")
    parser.add_argument("--consumer", action="append", required=True)
    parser.add_argument("--lock", action="append", default=[], metavar="OWNER/REPO=FILE",
                        help="offline lock input; otherwise retrieve actual lock from GitHub")
    parser.add_argument("--ref", default="main", help="consumer revision to resolve (default: main)")
    parser.add_argument("--output", help="write stable JSON report, otherwise stdout")
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        evidence = (json.loads(Path(args.review).read_text(encoding="utf-8"))
                    if args.review else None)
        validated = vendor_consumers.validate(registry)
        if len(set(args.consumer)) != len(args.consumer):
            raise ValueError("duplicate consumer")
        local = {}
        for value in args.lock:
            name, sep, filename = value.partition("=")
            if not sep or not name or not filename or name in local:
                raise ValueError("expected unique --lock OWNER/REPO=FILE")
            local[name] = json.loads(Path(filename).read_text(encoding="utf-8"))
        if set(local) - set(args.consumer):
            raise ValueError("--lock consumer was not selected")
        locks, metadata = {}, {}
        for name in args.consumer:
            consumer = vendor_consumers.get_consumer(validated, name)
            if consumer["state"] == "locked":
                if name in local:
                    locks[name] = local[name]
                else:
                    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
                    revision = fetch_revision(name, args.ref, token=token)
                    locks[name], metadata[name] = fetch_lock(
                        name, consumer["lock"], ref=revision, token=token)
        report = reconcile(catalog, validated, locks, consumers=args.consumer,
                           metadata=metadata, evidence=evidence)
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
            base64.binascii.Error, json.JSONDecodeError) as exc:
        print("vendor lock reconciliation: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
