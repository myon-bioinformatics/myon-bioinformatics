"""Verify exported workspace JSON with Python 3.11+. Usage: python verify.py workspace.json"""
import argparse
import base64
import binascii
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    data = json.loads(args.workspace.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not data.get("documents"):
        parser.error("expected a version 1 workspace with at least one document")
    failures = 0
    for document in data["documents"]:
        original = document["source"].encode("utf-8")
        try:
            restored = base64.b64decode(document["base64"], validate=True)
            matched = restored == original
        except (binascii.Error, ValueError):
            matched = False
        print(json.dumps({"name": document["name"], "bytes": len(original),
                          "match": matched}, ensure_ascii=False))
        failures += not matched
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
