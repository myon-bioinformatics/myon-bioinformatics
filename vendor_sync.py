"""Reproducible GitHub file placement and update candidates (stdlib only)."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

__version__ = "0.1.0"
__all__ = ["git_blob", "validate", "synchronize", "main"]

SCHEMA = "vendor-lock/1"
MAX_BYTES = 8 * 1024 * 1024


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("expected a relative POSIX path")
    parts = value.split("/")
    if any(p in ("", ".", "..", ".git") for p in parts) or any(ord(c) < 32 for c in value):
        raise ValueError("unsafe path: " + value)
    return value


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
        raise ValueError("expected a full lowercase hex digest")


def validate(lock):
    if not isinstance(lock, dict) or set(lock) != {"schema", "files"} or lock["schema"] != SCHEMA:
        raise ValueError("expected vendor-lock/1")
    if not isinstance(lock["files"], list) or not lock["files"]:
        raise ValueError("files must be a nonempty list")
    destinations = set()
    for item in lock["files"]:
        if not isinstance(item, dict) or set(item) != {
            "repository", "ref", "commit", "source", "destination", "blob_sha", "sha256"
        }:
            raise ValueError("invalid file fields")
        if not isinstance(item["repository"], str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item["repository"]
        ) or any(p in (".", "..") for p in item["repository"].split("/")):
            raise ValueError("expected owner/repository")
        _path(item["ref"])
        _path(item["source"])
        destination = _path(item["destination"])
        if destination.casefold() in destinations:
            raise ValueError("duplicate destination")
        destinations.add(destination.casefold())
        _hex(item["commit"], 40)
        _hex(item["blob_sha"], 40)
        _hex(item["sha256"], 64)
    for destination in destinations:
        if any(destination.startswith(other + "/") for other in destinations if other != destination):
            raise ValueError("overlapping destinations")
    return lock


def _target(root, relative):
    path = root.joinpath(*PurePosixPath(_path(relative)).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink destination: " + relative)
    if not path.resolve().is_relative_to(root):
        raise ValueError("destination outside root")
    return path


def _verify(item, data):
    if git_blob(data) != item["blob_sha"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("digest mismatch: " + item["destination"])


def _get(url):
    headers = {"User-Agent": "vendor-sync/1"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("download exceeds byte limit")
    return data


def _raw(item, get):
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        item["repository"], item["commit"], quote(item["source"], safe="/")
    )
    return get(url)


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) & 0o777 if path.exists() else 0o644
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def synchronize(manifest, root, mode, *, get=_get):
    """Check offline, place locked bytes, or resolve an upstream update candidate.

    All downloads/digests are validated before any write. Files are replaced
    atomically individually; filesystem I/O failure can leave a partial batch.
    No downloaded module is imported, and no Git mutation is performed here.
    """
    root = Path(root).resolve()
    manifest = _target(root, manifest)
    lock = validate(json.loads(manifest.read_text(encoding="utf-8")))
    for item in lock["files"]:
        target = _target(root, item["destination"])
        if target.relative_to(root).as_posix().casefold() == manifest.relative_to(root).as_posix().casefold():
            raise ValueError("file destination collides with manifest")
    if mode not in ("check", "materialize", "update"):
        raise ValueError("unknown mode")
    # Updates must start from the recorded bytes, never silently replace edits.
    if mode == "update":
        for item in lock["files"]:
            _verify(item, _target(root, item["destination"]).read_bytes())
    candidate = copy.deepcopy(lock)
    pending = []
    commits = {}
    for item in candidate["files"]:
        target = _target(root, item["destination"])
        if mode == "check":
            _verify(item, target.read_bytes())
            continue
        if mode == "update":
            key = item["repository"], item["ref"]
            if key not in commits:
                url = "https://api.github.com/repos/{}/commits?sha={}&per_page=1".format(
                    key[0], quote(key[1], safe="")
                )
                listed = json.loads(get(url))
                if not isinstance(listed, list) or not listed:
                    raise ValueError("upstream ref has no commits")
                commits[key] = listed[0]["sha"]
                _hex(commits[key], 40)
            item["commit"] = commits[key]
            url = "https://api.github.com/repos/{}/contents/{}?ref={}".format(
                item["repository"], quote(item["source"], safe="/"), item["commit"]
            )
            metadata = json.loads(get(url))
            if metadata.get("type") != "file":
                raise ValueError("source must be a regular file")
            item["blob_sha"] = metadata["sha"]
            _hex(item["blob_sha"], 40)
            data = _raw(item, get)
            item["sha256"] = hashlib.sha256(data).hexdigest()
        else:
            data = target.read_bytes() if target.is_file() else None
            if data is None or git_blob(data) != item["blob_sha"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                data = _raw(item, get)
        _verify(item, data)
        pending.append((target, data))
    # Unchanged upstream bytes do not churn pins on unrelated upstream commits.
    if mode == "update":
        for key in commits:
            old = [i for i in lock["files"] if (i["repository"], i["ref"]) == key]
            new = [i for i in candidate["files"] if (i["repository"], i["ref"]) == key]
            if all(a["blob_sha"] == b["blob_sha"] and a["sha256"] == b["sha256"] for a, b in zip(old, new)):
                for a, b in zip(old, new):
                    b["commit"] = a["commit"]
        validate(candidate)
        encoded = (json.dumps(candidate, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if candidate != lock:
            pending.append((manifest, encoded))
    changed = []
    for target, data in pending:
        if not target.exists() or target.read_bytes() != data:
            _atomic(target, data)
            changed.append(target.relative_to(root).as_posix())
    return {"schema": SCHEMA, "mode": mode, "changed_paths": changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "materialize", "update"))
    parser.add_argument("--manifest", default="vendor.lock.json")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    try:
        result = synchronize(args.manifest, args.root, args.mode)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("vendor-sync: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
