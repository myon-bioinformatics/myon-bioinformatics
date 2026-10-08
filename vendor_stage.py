#!/usr/bin/env python3
"""Canonical, offline vendor evidence staging for CI artifacts (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import vendor_sync

__version__ = "0.2.0"
__all__ = ["stage", "main"]
METADATA = "vendor-evidence.json"


def _regular(root, relative):
    path = vendor_sync._target(root, relative)
    if not path.is_file():
        raise ValueError("missing or non-file evidence member: " + relative)
    return path


def _disjoint(paths):
    seen = set()
    for path in paths:
        key = vendor_sync._path(path).casefold()
        if key in seen:
            raise ValueError("duplicate evidence member: " + path)
        seen.add(key)
    for path in seen:
        if any(path.startswith(other + "/") for other in seen if other != path):
            raise ValueError("overlapping evidence members")


def stage(root, manifest, output, *, kind="locked", runtime=(), legacy=(),
          promotion_receipt=None):
    """Stage verified lock bytes and explicitly classified supplemental files.

    An optional promotion receipt is included only for candidate runs and only
    if present. It is evidence, not proof that an update/test succeeded. Required
    runtime/legacy files must exist. No network or source code import occurs.
    Use an isolated checkout: concurrent filesystem changes are unsupported.
    """
    root = Path(root).resolve()
    target = vendor_sync._target(root, output)
    if target.exists():
        raise ValueError("existing staging output")
    if kind not in ("locked", "candidate"):
        raise ValueError("invalid evidence kind")
    # Reject unsafe manifest before reading it; evidence validates vendor-lock/1.
    _regular(root, manifest)
    runtime = list(runtime)
    if promotion_receipt is not None:
        receipt = vendor_sync._target(root, promotion_receipt)
        if kind == "candidate" and receipt.exists():
            _regular(root, promotion_receipt)
            runtime.append(promotion_receipt)
    evidence = vendor_sync.evidence(manifest, root, runtime=runtime)
    legacy = sorted(legacy, key=lambda p: (p.casefold(), p))
    members = evidence[kind] + evidence["runtime"] + legacy
    _disjoint(members + [METADATA])
    output_key = output.casefold()
    for member in members + [METADATA]:
        key = member.casefold()
        if key == output_key or key.startswith(output_key + "/") or output_key.startswith(key + "/"):
            raise ValueError("staging overlaps evidence source")
    # Read and verify all bytes before creating the output directory.
    payloads = {member: _regular(root, member).read_bytes() for member in members}
    lock = vendor_sync.validate(json.loads(payloads[manifest].decode("utf-8")))
    for entry in lock["files"]:
        vendor_sync._verify(entry, payloads[entry["destination"]])
    evidence.update(kind=kind, legacy=legacy,
                    sha256={member: hashlib.sha256(data).hexdigest()
                            for member, data in payloads.items()})
    # Exclusive leaf mkdir refuses a target that appeared after validation.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    try:
        for relative, data in payloads.items():
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (target / METADATA).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(target)
        raise
    return {"schema": "vendor-stage/1", "kind": kind, "output": str(target),
            "members": list(payloads)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".")
    p.add_argument("--manifest", default="vendor.lock.json")
    p.add_argument("--output", required=True)
    p.add_argument("--kind", choices=("locked", "candidate"), default="locked")
    p.add_argument("--runtime-evidence", action="append", default=[])
    p.add_argument("--legacy-evidence", action="append", default=[])
    p.add_argument("--promotion-receipt")
    args = p.parse_args(argv)
    try:
        result = stage(args.root, args.manifest, args.output, kind=args.kind,
                       runtime=args.runtime_evidence, legacy=args.legacy_evidence,
                       promotion_receipt=args.promotion_receipt)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("vendor-stage: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
