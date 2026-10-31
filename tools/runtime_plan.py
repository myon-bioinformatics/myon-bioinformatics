#!/usr/bin/env python3
"""Read-only nano profile planning from explicit JSON, using Python 3.9+ stdlib.

No host inventory, imports of described sources, native loading, commands,
network, installs, writes, or vendor adoption. A match is not execution readiness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys


SCHEMA = "nano-profiles/1"
ENV_SCHEMA = "nano-environment/1"
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 128
LANES = ("runtime", "optional", "build", "development")
GAPS = ("dependency_closure", "package_hashes", "security_review", "measurements")
NAME = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?\Z")
EVIDENCE_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/blob/([0-9a-f]{40})/(.+)\Z"
)


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(label + ": unexpected or missing fields")


def _text(value, label, pattern=None):
    if not isinstance(value, str) or not value or len(value) > 2000:
        raise ValueError(label + ": expected nonempty bounded text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(label + ": control characters are not allowed")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(label + ": invalid value")
    return value


def _list(value, label):
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError(label + ": expected bounded list")


def _strings(value, label):
    _list(value, label)
    for item in value:
        _text(item, label)
    if len(value) != len(set(value)):
        raise ValueError(label + ": duplicates are not allowed")


def _path(value):
    _text(value, "source path")
    if (not re.fullmatch(r"[A-Za-z0-9_./-]+", value) or value.startswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ValueError("source path: expected a relative repository path")


def _version(value):
    _text(value, "version", VERSION)
    parts = [int(part) for part in value.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def _expected(check):
    kind, value = check["kind"], check["expected"]
    if kind == "minimum_version":
        _version(value)
    elif kind == "equal":
        if type(value) is int:
            if not 0 <= value <= 2**31 - 1:
                raise ValueError("expected: integer out of range")
        else:
            _text(value, "expected")
    elif kind == "contains_all":
        _strings(value, "expected")
        if not value:
            raise ValueError("expected: nonempty symbols required")
    elif kind == "present":
        if value is not True:
            raise ValueError("present requires expected=true")
    else:
        raise ValueError("unsupported check kind")


def validate_manifest(manifest):
    """Strict v1 shape validation; source references are not fetched or adopted."""
    _object(manifest, ("schema", "profiles"), "manifest")
    if manifest["schema"] != SCHEMA:
        raise ValueError("unsupported manifest schema")
    profiles = manifest["profiles"]
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= MAX_ITEMS:
        raise ValueError("profiles: expected nonempty bounded object")
    fact_kinds = {}
    for name, profile in profiles.items():
        _text(name, "profile name", NAME)
        _object(profile, ("description", "sources", "requirements", "evidence"), name)
        _text(profile["description"], "description")
        _object(profile["evidence"], GAPS, "evidence")
        if any(value is not None for value in profile["evidence"].values()):
            raise ValueError("v1 evidence fields must remain null (not verified)")
        _list(profile["sources"], "sources")
        seen_sources = set()
        for source in profile["sources"]:
            _object(source, ("repository", "path", "scope", "ledger", "evidence_url"), "source")
            _text(source["repository"], "repository", REPOSITORY)
            _path(source["path"])
            if source["scope"] not in LANES:
                raise ValueError("source: unsupported scope")
            key = (source["repository"], source["path"], source["scope"])
            if key in seen_sources:
                raise ValueError("duplicate source")
            seen_sources.add(key)
            # References only: no second ledger or mutable branch pin.
            if source["ledger"] == "vendor-catalog.json":
                if source["evidence_url"] is not None:
                    raise ValueError("catalog reference must not duplicate a source pin")
            elif source["ledger"] is None:
                match = EVIDENCE_URL.fullmatch(_text(source["evidence_url"], "evidence URL"))
                if not match or match[1] != source["repository"] or match[3] != source["path"]:
                    raise ValueError("upstream evidence must name the same source at a full SHA")
            else:
                raise ValueError("unsupported source ledger")
        _object(profile["requirements"], LANES, "requirements")
        if not profile["requirements"]["runtime"]:
            raise ValueError("runtime requirements must not be empty")
        seen = set()
        for lane in LANES:
            checks = profile["requirements"][lane]
            _list(checks, "requirements lane")
            for check in checks:
                _object(check, ("id", "fact", "kind", "expected"), "check")
                _text(check["id"], "check id", NAME)
                _text(check["fact"], "fact name", NAME)
                if check["id"] in seen:
                    raise ValueError("duplicate check id")
                seen.add(check["id"])
                _expected(check)
                signature = (check["kind"], type(check["expected"]))
                previous = fact_kinds.setdefault(check["fact"], signature)
                if previous != signature:
                    raise ValueError("inconsistent fact type")
    return manifest


def validate_environment(environment, manifest):
    _object(environment, ("schema", "kind", "facts"), "environment")
    if environment["schema"] != ENV_SCHEMA or environment["kind"] not in ("synthetic", "provided"):
        raise ValueError("unsupported environment schema or kind")
    facts = environment["facts"]
    if not isinstance(facts, dict) or len(facts) > MAX_ITEMS:
        raise ValueError("facts: expected bounded object")
    checks = {check["fact"]: check for profile in manifest["profiles"].values()
              for lane in LANES for check in profile["requirements"][lane]}
    for key, value in facts.items():
        if key not in checks:
            raise ValueError("unknown environment fact")
        if value is None or value is False:
            continue  # unknown and explicitly absent, respectively
        check = checks[key]
        kind = check["kind"]
        if kind == "minimum_version":
            _version(value)
        elif kind == "contains_all":
            _strings(value, "observed symbols")
        elif kind == "present":
            if value is not True:
                raise ValueError("presence fact must be true, false or null")
        elif type(value) is not type(check["expected"]):
            raise ValueError("fact type mismatch")
        elif isinstance(value, str):
            _text(value, "fact")
        elif not 0 <= value <= 2**31 - 1:
            raise ValueError("fact integer out of range")
    return environment


def _check(check, facts):
    value = facts.get(check["fact"])
    expected = check["expected"]
    if value is None:
        status = "unknown"
    elif value is False:
        status = "missing"
    elif check["kind"] == "minimum_version":
        status = "met" if _version(value) >= _version(expected) else "unsupported"
    elif check["kind"] == "contains_all":
        status = "met" if set(expected).issubset(value) else "unsupported"
    else:
        status = "met" if value == expected else "unsupported"
    return dict(check, actual=value, status=status)


def plan(manifest, profile_name, environment):
    """Compare caller-declared facts only. Never return execution_ready=true."""
    validate_manifest(manifest)
    validate_environment(environment, manifest)
    _text(profile_name, "profile name", NAME)
    if profile_name not in manifest["profiles"]:
        raise ValueError("unknown profile")
    profile = manifest["profiles"][profile_name]
    lanes = {lane: [_check(check, environment["facts"]) for check in profile["requirements"][lane]]
             for lane in LANES}
    runtime = {check["status"] for check in lanes["runtime"]}
    status = next((state for state in ("unsupported", "missing", "unknown") if state in runtime),
                  "requirements_match")
    return {"schema": "nano-plan/1", "profile": profile_name, "static_requirements_status": status,
            "execution_verification": "not_performed", "input_kind": environment["kind"], "measured": False, "execution_ready": False,
            "requirements": lanes, "sources": profile["sources"],
            "source_verification": "not_performed", "adoption": "unchanged",
            "evidence": profile["evidence"], "evidence_gaps": list(GAPS)}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite JSON number")


def _bounded_nesting(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds 32 levels")
        elif char in "]}":
            depth -= 1  # malformed nesting is rejected by json.loads


def load_json(path):
    """Read one explicitly supplied regular JSON file, at most 1 MiB."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("input must be a regular, non-symlink JSON file")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("JSON input exceeds 1 MiB")
    try:
        text = data.decode("utf-8")
        _bounded_nesting(text)
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid JSON encoding or nesting") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, help="explicit nano-profiles/1 JSON file")
    parser.add_argument("--profile", required=True, help="profile ID, never an executable path")
    parser.add_argument("--environment", required=True, help="explicit facts JSON; not collected from the host")
    args = parser.parse_args(argv)
    try:
        result = plan(load_json(args.profiles), args.profile, load_json(args.environment))
    except (ValueError, OSError) as exc:
        print(json.dumps({"schema": "nano-plan-error/1", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["static_requirements_status"] == "requirements_match" else 1


if __name__ == "__main__":
    sys.exit(main())
