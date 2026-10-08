#!/usr/bin/env python3
"""Canonical, read-only vendor evidence staging for CI artifacts (stdlib only)."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import vendor_sync

__version__ = "0.1.0"
__all__ = ["stage", "main"]

def stage(root, manifest, output, *, kind="locked", runtime=(), legacy=()):
    root = Path(root).resolve()
    target = (root / output).resolve()
    if target == root or not target.is_relative_to(root) or target.exists():
        raise ValueError("unsafe or existing staging output")
    if kind not in ("locked", "candidate"):
        raise ValueError("invalid evidence kind")
    evidence = vendor_sync.evidence(manifest, root, runtime=runtime)
    members = evidence[kind] + evidence["runtime"] + list(legacy)
    validated = []
    seen = set()
    for member in members:
        relative = vendor_sync._path(member)
        key = relative.casefold()
        if key in seen:
            raise ValueError("duplicate evidence member: " + relative)
        seen.add(key)
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file() or (root / relative).is_symlink():
            raise ValueError("unsafe or missing evidence member: " + relative)
        if source == target or target in source.parents or source in target.parents:
            raise ValueError("staging overlaps evidence source")
        validated.append((relative, source))
    target.mkdir(parents=True)
    for relative, source in validated:
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    (target / "vendor-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"schema": "vendor-stage/1", "kind": kind, "output": str(target),
            "members": [item for item, _ in validated]}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".")
    p.add_argument("--manifest", default="vendor.lock.json")
    p.add_argument("--output", required=True)
    p.add_argument("--kind", choices=("locked", "candidate"), default="locked")
    p.add_argument("--runtime-evidence", action="append", default=[])
    p.add_argument("--legacy-evidence", action="append", default=[])
    args = p.parse_args(argv)
    try:
        result = stage(args.root, args.manifest, args.output, kind=args.kind,
                       runtime=args.runtime_evidence, legacy=args.legacy_evidence)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("vendor-stage: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
