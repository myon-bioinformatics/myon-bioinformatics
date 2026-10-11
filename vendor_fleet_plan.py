"""Read-only vendor fleet planning from reviewed catalog and consumer topology.

Does not access the network, edit locks, or assert candidate compatibility.
"""
import argparse
import json
from pathlib import Path
import sys

import vendor_catalog
import vendor_consumers


def plan(catalog, registry, *, active_consumers=None):
    """Identify catalog registrations to review, without guessing file destinations.

    active_consumers optionally limits work to explicitly selected repositories.
    Legacy consumers are reported separately; neither missing locks nor absent
    source entries imply a successful enrollment or compatible upgrade.
    """
    catalog = vendor_catalog.validate(catalog)
    registry = vendor_consumers.validate(registry)
    selected = None if active_consumers is None else {x.casefold() for x in active_consumers}
    known = {entry["repository"].casefold(): entry for entry in registry["consumers"]}
    if selected is not None:
        unknown = selected - known.keys()
        if unknown:
            raise ValueError("unknown consumers: " + ", ".join(sorted(unknown)))
    rows = []
    for consumer in registry["consumers"]:
        name = consumer["repository"]
        if selected is not None and name.casefold() not in selected:
            continue
        declared = {(item["repository"].casefold(), item["source"]): item
                    for item in consumer["entries"]}
        for tool in catalog["tools"]:
            decision = next(
                (value for key, value in tool.get("consumers", {}).items()
                 if key.casefold() == name.casefold()),
                tool.get("default_enrollment",
                         {"enrollment": "enrolled", "reason": "default_single_file_policy"}),
            )
            key = (tool["repository"].casefold(), tool["source"])
            match = declared.get(key)
            if consumer["state"] == "legacy":
                action = "legacy_consumer_review"
            elif match is not None:
                action = "registered"
            elif decision["enrollment"] == "skipped":
                action = "skipped"
            else:
                action = "needs_registration"
            rows.append({
                "consumer": name,
                "tool_repository": tool["repository"],
                "source": tool["source"],
                "recommended_commit": tool["commit"],
                "enrollment": decision["enrollment"],
                "reason": decision["reason"],
                "action": action,
                "destination": None if match is None else match["destination"],
            })
    return {
        "schema": "vendor-fleet-plan/1",
        "read_only": True,
        "candidate_compatibility": "unverified",
        "rows": rows,
        "counts": {state: sum(r["action"] == state for r in rows)
                   for state in ("registered", "needs_registration", "skipped",
                                 "legacy_consumer_review")},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="vendor-catalog.json")
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--consumer", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        cat = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        reg = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        result = plan(cat, reg, active_consumers=args.consumer or None)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"vendor fleet plan: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
