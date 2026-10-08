"""Validate the organization-wide vendor consumer topology (stdlib only)."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

import vendor_sync

SCHEMA = "vendor-consumers/1"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REASON = re.compile(r"[a-z][a-z0-9_]*")


def _repository(value):
    if (
        not isinstance(value, str)
        or not _REPO.fullmatch(value)
        or any(part in (".", "..") for part in value.split("/"))
    ):
        raise ValueError("expected owner/repository")
    return value


def _paths(values, field):
    if not isinstance(values, list) or not values:
        raise ValueError(field + " must be a nonempty list")
    seen = set()
    result = []
    for value in values:
        value = vendor_sync._path(value)
        key = value.casefold()
        if key in seen:
            raise ValueError("duplicate " + field + " path")
        seen.add(key)
        result.append(value)
    return result


def validate(registry):
    """Validate and return a normalized copy without mutating caller data."""
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema", "consumers"}
        or registry["schema"] != SCHEMA
    ):
        raise ValueError("expected vendor-consumers/1")
    if not isinstance(registry["consumers"], list) or not registry["consumers"]:
        raise ValueError("consumers must be a nonempty list")

    result = copy.deepcopy(registry)
    names = set()
    for consumer in result["consumers"]:
        required = {"repository", "state", "lock", "entries"}
        optional = {"reason", "legacy_provenance"}
        if (
            not isinstance(consumer, dict)
            or not required <= consumer.keys()
            or consumer.keys() - required - optional
        ):
            raise ValueError("invalid consumer fields")

        repository = _repository(consumer["repository"])
        folded = repository.casefold()
        if folded in names:
            raise ValueError("duplicate consumer repository")
        names.add(folded)

        state = consumer["state"]
        if state not in ("locked", "legacy"):
            raise ValueError("consumer state must be locked or legacy")

        if state == "locked":
            if not isinstance(consumer["lock"], str):
                raise ValueError("locked consumer requires lock path")
            vendor_sync._path(consumer["lock"])
            if "reason" in consumer or "legacy_provenance" in consumer:
                raise ValueError("locked consumer cannot declare legacy fields")
        else:
            if consumer["lock"] is not None:
                raise ValueError("legacy consumer lock must be null")
            if (
                not isinstance(consumer.get("reason"), str)
                or not _REASON.fullmatch(consumer["reason"])
            ):
                raise ValueError("legacy consumer requires machine-readable reason")
            consumer["legacy_provenance"] = _paths(
                consumer.get("legacy_provenance"), "legacy_provenance"
            )

        if not isinstance(consumer["entries"], list) or not consumer["entries"]:
            raise ValueError("consumer entries must be a nonempty list")
        destinations = set()
        identities = set()
        for entry in consumer["entries"]:
            if not isinstance(entry, dict) or set(entry) != {
                "repository",
                "source",
                "destination",
            }:
                raise ValueError("invalid consumer entry fields")
            source_repository = _repository(entry["repository"])
            if source_repository.casefold() == folded:
                raise ValueError("consumer must not vendor its own canonical artifact")
            source = vendor_sync._path(entry["source"])
            destination = vendor_sync._path(entry["destination"])
            destination_key = destination.casefold()
            if destination_key in destinations:
                raise ValueError("duplicate consumer destination")
            destinations.add(destination_key)
            identity = (source_repository.casefold(), source, destination_key)
            if identity in identities:
                raise ValueError("duplicate consumer entry")
            identities.add(identity)
    return result


def get_consumer(registry, repository):
    registry = validate(registry)
    repository = _repository(repository)
    for consumer in registry["consumers"]:
        if consumer["repository"].casefold() == repository.casefold():
            return consumer
    raise ValueError("consumer not registered: " + repository)


def compare(registry, lock, repository):
    """Compare topology only; exact commit/blob/SHA-256 remain lock-owned."""
    consumer = get_consumer(registry, repository)
    if consumer["state"] != "locked":
        raise ValueError("legacy consumer has no vendor-lock/1 to compare")
    vendor_sync.validate(lock)

    expected = {
        (entry["repository"].casefold(), entry["source"], entry["destination"].casefold()):
        copy.deepcopy(entry)
        for entry in consumer["entries"]
    }
    actual = {
        (entry["repository"].casefold(), entry["source"], entry["destination"].casefold()): {
            "repository": entry["repository"],
            "source": entry["source"],
            "destination": entry["destination"],
        }
        for entry in lock["files"]
    }

    missing_keys = expected.keys() - actual.keys()
    unexpected_keys = actual.keys() - expected.keys()

    def rows(mapping, keys):
        return [mapping[key] for key in sorted(keys)]

    return {
        "schema": "vendor-consumer-comparison/1",
        "consumer": consumer["repository"],
        "lock": consumer["lock"],
        "matches": not missing_keys and not unexpected_keys,
        "missing": rows(expected, missing_keys),
        "unexpected": rows(actual, unexpected_keys),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "compare"))
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--consumer")
    parser.add_argument("--lock")
    args = parser.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        if args.mode == "compare":
            if not args.consumer or not args.lock:
                raise ValueError("compare requires --consumer and --lock")
            result = compare(
                registry,
                json.loads(Path(args.lock).read_text(encoding="utf-8")),
                args.consumer,
            )
        else:
            result = validate(registry)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print("vendor-consumers: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
