"""Reproducible GitHub file placement and update candidates (stdlib only)."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

__version__ = "0.3.0"
__all__ = [
    "git_blob",
    "inspect_source",
    "validate",
    "evidence",
    "synchronize",
    "promote",
    "commit_merged_into_default",
    "collect_lock_pins",
    "collect_workflow_pins",
    "audit_consumer_merged_pins",
    "audit_registry_merged_pins",
    "validate_child_heads",
    "refresh_child_heads",
    "check_child_heads",
    "run_parent_child_ci",
    "resolve_parent_pr",
    "validate_parent_pr_ref",
    "verify_reference_identity",
    "source_diff_report",
    "main",
]

PARENT_PR_REF_SCHEMA = "parent-pr-ref/1"
AUTOFIX_BRANCH_PREFIX = "vendor/child-heads-sync"

SCHEMA = "vendor-lock/1"
HTML_IMPORT_SCHEMA = "html-import-vendor/1"
CHILD_HEADS_SCHEMA = "child-heads/1"
PARENT_PR_RESOLVE_SCHEMA = "parent-pr-resolve/1"
PARENT_REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
HTML_IMPORT_LOCK_CANDIDATES = ("tools/html-import/vendor.lock.json",)
MAX_BYTES = 8 * 1024 * 1024
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


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
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("download exceeds byte limit")
    return data


def _public_git_snapshot(repository, ref, sources, *, remote=None):
    """Read public Git objects in a temporary bare repo on API rate limits."""
    with tempfile.TemporaryDirectory(prefix="vendor-public-git-") as directory:
        # Do not inherit repository selectors or injected credential/config state.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        command = ["git", "-c", "credential.helper=", "-c", "core.askPass=",
                   "-c", "http.extraHeader=", "-C", directory]

        def git(*args):
            result = subprocess.run(command + list(args), env=env, capture_output=True, timeout=60)
            if result.returncode:
                raise ValueError("public Git fetch/read failed: " + result.stderr.decode("utf-8", "replace").strip())
            return result.stdout

        git("init", "--bare")
        git("fetch", "--no-tags", "--depth=1", "--filter=blob:none", "--",
            remote or "https://github.com/" + repository + ".git", ref)
        commit = git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        _hex(commit, 40)
        files = {}
        for source in sources:
            obj = commit + ":" + source
            record = git("ls-tree", commit, "--", source).split(b"\t", 1)[0].split()
            if len(record) != 3 or record[0] not in (b"100644", b"100755") or record[1] != b"blob":
                raise ValueError("source must be a regular file")
            blob = record[2].decode()
            size = int(git("cat-file", "-s", obj))
            if size > MAX_BYTES:
                raise ValueError("download exceeds byte limit")
            files[source] = (blob, git("cat-file", "blob", obj))
        return commit, files


def inspect_source(repository, commit, source):
    """Read one exact public source without placing files or updating a lock.

    Git tree mode, resolved commit, Git blob and SHA-256 are checked here;
    recommendation metadata never supplies trusted source digests.
    """
    item = dict(repository=repository, ref=commit, commit=commit, source=source,
                destination="source", blob_sha="0" * 40, sha256="0" * 64)
    validate({"schema": SCHEMA, "files": [item]})
    resolved, files = _public_git_snapshot(repository, commit, [source])
    if resolved != commit:
        raise ValueError("public Git commit does not match recommended SHA")
    item["blob_sha"], data = files[source]
    _hex(item["blob_sha"], 40)
    item["sha256"] = hashlib.sha256(data).hexdigest()
    _verify(item, data)
    return {key: item[key] for key in ("repository", "commit", "source", "blob_sha", "sha256")}


def _limited(error):
    return isinstance(error, HTTPError) and error.code in (403, 429)


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


def _create(path, data):
    """Place complete bytes without replacing a destination that appeared late."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
        os.chmod(name, 0o644)
        os.link(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def evidence(manifest, root, *, runtime=None):
    """Derive deterministic evidence membership from a validated vendor lock."""
    root = Path(root).resolve()
    manifest_path = _target(root, manifest)
    lock = validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    locked = [manifest_path.relative_to(root).as_posix()]
    locked.extend(item["destination"] for item in lock["files"])
    folded = [p.casefold() for p in locked]
    if len(folded) != len(set(folded)):
        raise ValueError("locked evidence membership collides")
    locked.sort(key=lambda p: (p.casefold(), p))
    runtime_paths = []
    seen = set(folded)
    for value in runtime or []:
        path = _path(value)
        key = path.casefold()
        if key in folded:
            raise ValueError("runtime evidence collides with locked membership: " + path)
        if key not in seen:
            runtime_paths.append(path)
            seen.add(key)
    runtime_paths.sort(key=lambda p: (p.casefold(), p))
    return {"schema": "vendor-evidence/1", "locked": locked,
            "candidate": list(locked), "runtime": runtime_paths}


def synchronize(manifest, root, mode, *, get=_get):
    """Check offline, place locked bytes, or resolve an upstream update candidate.

    All downloads/digests are validated before any write. Files are placed
    atomically individually; filesystem I/O failure can leave a partial batch.
    Enrollment creates only initially missing destinations, using hard links
    to prevent replacement of a file that appears during the command. Use an
    isolated checkout; concurrent directory/manifest changes are unsupported.
    No downloaded module is imported. Public Git fallback only writes a temporary
    bare object store; the consumer Git checkout and remote refs are untouched.
    """
    root = Path(root).resolve()
    manifest = _target(root, manifest)
    lock = validate(json.loads(manifest.read_text(encoding="utf-8")))
    for item in lock["files"]:
        target = _target(root, item["destination"])
        if target.relative_to(root).as_posix().casefold() == manifest.relative_to(root).as_posix().casefold():
            raise ValueError("file destination collides with manifest")
    if mode not in ("check", "materialize", "update", "enroll"):
        raise ValueError("unknown mode")
    # Enrollment may create missing locked files, but never repairs an existing edit.
    missing = set()
    if mode == "enroll":
        for item in lock["files"]:
            target = _target(root, item["destination"])
            if target.exists():
                _verify(item, target.read_bytes())
            else:
                missing.add(item["destination"])
    # Updates must start from the recorded bytes, never silently replace edits.
    if mode == "update":
        for item in lock["files"]:
            _verify(item, _target(root, item["destination"]).read_bytes())
    candidate = copy.deepcopy(lock)
    pending = []
    commits = {}
    snapshots = {}
    for item in candidate["files"]:
        target = _target(root, item["destination"])
        if mode == "check":
            _verify(item, target.read_bytes())
            continue
        if mode == "enroll" and item["destination"] not in missing:
            continue
        if mode == "update":
            key = item["repository"], item["ref"]
            sources = [i["source"] for i in candidate["files"]
                       if (i["repository"], i["ref"]) == key]
            if key not in commits:
                url = "https://api.github.com/repos/{}/commits?sha={}&per_page=1".format(
                    key[0], quote(key[1], safe="")
                )
                try:
                    listed = json.loads(get(url))
                    if not isinstance(listed, list) or not listed:
                        raise ValueError("upstream ref has no commits")
                    commits[key] = listed[0]["sha"]
                    _hex(commits[key], 40)
                except HTTPError as error:
                    if not _limited(error):
                        raise
                    commits[key], snapshots[key] = _public_git_snapshot(key[0], key[1], sources)
            item["commit"] = commits[key]
            if key not in snapshots:
                url = "https://api.github.com/repos/{}/contents/{}?ref={}".format(
                    item["repository"], quote(item["source"], safe="/"), item["commit"]
                )
                try:
                    metadata = json.loads(get(url))
                    if metadata.get("type") != "file":
                        raise ValueError("source must be a regular file")
                    item["blob_sha"] = metadata["sha"]
                    _hex(item["blob_sha"], 40)
                    data = _raw(item, get)
                except HTTPError as error:
                    if not _limited(error):
                        raise
                    resolved, snapshots[key] = _public_git_snapshot(key[0], commits[key], sources)
                    if resolved != commits[key]:
                        raise ValueError("public Git commit does not match resolved SHA")
            if key in snapshots:
                item["blob_sha"], data = snapshots[key][item["source"]]
            item["sha256"] = hashlib.sha256(data).hexdigest()
        else:
            data = target.read_bytes() if mode != "enroll" and target.is_file() else None
            if data is None or git_blob(data) != item["blob_sha"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                key = item["repository"], item["commit"]
                if key not in snapshots:
                    try:
                        data = _raw(item, get)
                    except HTTPError as error:
                        if not _limited(error):
                            raise
                        sources = [i["source"] for i in candidate["files"]
                                   if (i["repository"], i["commit"]) == key
                                   and (mode != "enroll" or i["destination"] in missing)]
                        resolved, snapshots[key] = _public_git_snapshot(key[0], key[1], sources)
                        if resolved != key[1]:
                            raise ValueError("public Git commit does not match locked SHA")
                if key in snapshots:
                    blob, data = snapshots[key][item["source"]]
                    if blob != item["blob_sha"]:
                        raise ValueError("public Git blob does not match locked blob")
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
    if mode == "enroll":
        # Recheck the whole baseline before any placement: downloads can be slow.
        for item in lock["files"]:
            target = _target(root, item["destination"])
            if item["destination"] not in missing or target.exists():
                _verify(item, target.read_bytes())
    changed = []
    for target, data in pending:
        if mode == "enroll":
            relative = target.relative_to(root).as_posix()
            target = _target(root, relative)
            try:
                _create(target, data)
            except FileExistsError:
                target = _target(root, relative)
                if target.read_bytes() != data:
                    raise ValueError("digest mismatch: " + relative)
            else:
                changed.append(relative)
            continue
        if not target.exists() or target.read_bytes() != data:
            _atomic(target, data)
            changed.append(target.relative_to(root).as_posix())
    if mode == "enroll":
        synchronize(manifest.relative_to(root).as_posix(), root, "check", get=get)
    return {"schema": SCHEMA, "mode": mode, "changed_paths": changed}


def _git_subprocess_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _normalize_repository(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected repository")
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/").rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not _REPO.fullmatch(value) or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("expected owner/repository")
    return value


def _repository_remote(repository, remotes=None):
    remotes = remotes or {}
    if repository in remotes:
        return remotes[repository]
    return "https://github.com/" + repository + ".git"


def _git_run(argv, *, timeout=120):
    result = subprocess.run(argv, env=_git_subprocess_env(), capture_output=True, timeout=timeout)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or result.stdout.decode("utf-8", "replace").strip()
        raise ValueError("git failed: " + detail)
    return result.stdout


def _default_branch_name(remote):
    output = _git_run(["git", "ls-remote", "--symref", remote, "HEAD"]).decode("utf-8", "replace")
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.split("refs/heads/", 1)[1].split("\t", 1)[0]
    raise ValueError("cannot resolve default branch for " + remote)


def _git_branch_tip(remote, branch):
    output = _git_run(["git", "ls-remote", "--heads", remote, branch]).decode("utf-8", "replace").strip()
    if not output:
        raise ValueError("missing branch " + branch + " on " + remote)
    sha = output.split()[0]
    _hex(sha, 40)
    return sha


def _locked_consumer_names(registry):
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    return sorted(
        (entry["repository"] for entry in registry["consumers"] if entry["state"] == "locked"),
        key=str.casefold,
    )


def _coerce_repository_id(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("expected repository id")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("expected positive repository id")
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("expected positive repository id")
        return parsed
    raise ValueError("expected repository id")


def _normalize_optional_signals(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("optional signals must be an object")
    return dict(value)


def _normalize_head_entry(value):
    if isinstance(value, str):
        raise ValueError("child head entries must be objects with sha and repository_id")
    if not isinstance(value, dict):
        raise ValueError("invalid child head entry")
    sha = value.get("sha")
    _hex(sha, 40)
    entry = {
        "sha": sha,
        "repository_id": _coerce_repository_id(value.get("repository_id")),
    }
    optional = value.get("optional")
    if optional is not None:
        entry["optional"] = _normalize_optional_signals(optional)
    return entry


def validate_child_heads(document):
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "ref", "heads"}
        or document["schema"] != CHILD_HEADS_SCHEMA
    ):
        raise ValueError("expected child-heads/1")
    ref = document["ref"]
    if not isinstance(ref, str) or not ref or any(ord(c) < 32 for c in ref):
        raise ValueError("invalid child-heads ref")
    heads = document["heads"]
    if not isinstance(heads, dict) or not heads:
        raise ValueError("child heads must be a nonempty object")
    normalized = {}
    for name, entry in heads.items():
        normalized[_normalize_repository(name)] = _normalize_head_entry(entry)
    return {"schema": CHILD_HEADS_SCHEMA, "ref": ref, "heads": normalized}


def validate_parent_pr_ref(document):
    if not isinstance(document, dict) or document.get("schema") != PARENT_PR_REF_SCHEMA:
        raise ValueError("expected parent-pr-ref/1")
    repository = _normalize_repository(document.get("repository"))
    pull_number = document.get("pull_number")
    if isinstance(pull_number, str) and pull_number.isdigit():
        pull_number = int(pull_number)
    if not isinstance(pull_number, int) or pull_number < 1:
        raise ValueError("expected positive pull_number")
    repository_id = _coerce_repository_id(document.get("repository_id"))
    result = {
        "schema": PARENT_PR_REF_SCHEMA,
        "repository": repository,
        "pull_number": pull_number,
        "repository_id": repository_id,
    }
    commit = document.get("commit")
    if commit is not None:
        _hex(commit, 40)
        result["commit"] = commit
    optional = document.get("optional")
    if optional is not None:
        result["optional"] = _normalize_optional_signals(optional)
    return result


def verify_reference_identity(*, expected_sha, expected_pull_number, expected_repository_id,
                              resolved_sha, resolved_pull_number, resolved_repository_id,
                              optional_recorded=None, optional_live=None):
    """Match on SHA (primary), pull number (secondary), repository id (required)."""
    _hex(expected_sha, 40)
    _hex(resolved_sha, 40)
    expected_repository_id = _coerce_repository_id(expected_repository_id)
    resolved_repository_id = _coerce_repository_id(resolved_repository_id)
    errors = []
    if resolved_sha != expected_sha:
        errors.append(
            "sha mismatch for repository id {rid}: expected {exp} got {got}".format(
                rid=expected_repository_id, exp=expected_sha, got=resolved_sha,
            )
        )
    if expected_pull_number is not None:
        if not isinstance(expected_pull_number, int) or expected_pull_number < 1:
            raise ValueError("expected positive pull number")
        if resolved_pull_number != expected_pull_number:
            errors.append(
                "pull number mismatch for repository id {rid}: expected {exp} got {got}".format(
                    rid=expected_repository_id, exp=expected_pull_number, got=resolved_pull_number,
                )
            )
    if resolved_repository_id != expected_repository_id:
        errors.append(
            "repository id mismatch: expected {exp} got {got}".format(
                exp=expected_repository_id, got=resolved_repository_id,
            )
        )
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "matched": True,
        "optional_recorded": _normalize_optional_signals(optional_recorded),
        "optional_live": _normalize_optional_signals(optional_live),
    }


def fetch_repository_record(repository, token=None, repo_fetch=None):
    repository = _normalize_repository(repository)
    if repo_fetch is not None:
        record = repo_fetch(repository)
    else:
        record = _github_api("GET", "https://api.github.com/repos/" + repository, token)
    if not isinstance(record, dict) or "id" not in record:
        raise ValueError("invalid repository response for " + repository)
    return record


def fetch_child_head(repository, ref="main", *, remotes=None):
    repository = _normalize_repository(repository)
    remote = _repository_remote(repository, remotes)
    if ref in ("main", "master"):
        try:
            branch = _default_branch_name(remote)
        except ValueError:
            branch = ref
    else:
        branch = ref
    return _git_branch_tip(remote, branch)


def fetch_child_head_record(repository, ref="main", *, remotes=None, token=None, repo_fetch=None):
    sha = fetch_child_head(repository, ref, remotes=remotes)
    record = fetch_repository_record(repository, token, repo_fetch=repo_fetch)
    entry = {"sha": sha, "repository_id": _coerce_repository_id(record["id"])}
    optional = {}
    if isinstance(record.get("created_at"), str):
        optional["repository_created_at"] = record["created_at"]
    if optional:
        entry["optional"] = optional
    return entry


def refresh_child_heads(registry, heads_path, *, ref="main", remotes=None, token=None, repo_fetch=None):
    names = _locked_consumer_names(registry)
    heads = {
        name: fetch_child_head_record(name, ref, remotes=remotes, token=token, repo_fetch=repo_fetch)
        for name in names
    }
    document = validate_child_heads({"schema": CHILD_HEADS_SCHEMA, "ref": ref, "heads": heads})
    encoded = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    Path(heads_path).write_text(encoded, encoding="utf-8")
    return document


def _github_api(method, url, token=None, body=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "vendor-sync/parent-child-ci"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=60) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("GitHub response exceeds byte limit")
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _repository_archived(repository, token=None):
    record = _github_api("GET", "https://api.github.com/repos/" + _normalize_repository(repository), token)
    return bool(record and record.get("archived"))


def _list_remote_branches(repository, *, remotes=None, local_roots=None):
    repository = _normalize_repository(repository)
    local_roots = local_roots or {}
    if repository in local_roots:
        root = Path(local_roots[repository])
        output = _git_run(["git", "-C", str(root), "for-each-ref", "--format=%(refname:short)",
                           "refs/heads"]).decode("utf-8", "replace")
        return sorted(line for line in output.splitlines() if line and line != "HEAD")
    remote = _repository_remote(repository, remotes)
    output = _git_run(["git", "ls-remote", "--heads", remote]).decode("utf-8", "replace")
    branches = []
    for line in output.splitlines():
        if not line:
            continue
        _, ref = line.split("\t", 1)
        branches.append(ref.split("/", 2)[-1])
    return sorted(set(branches))


def branches_differing_from_main(repository, *, remotes=None, local_roots=None, default_branch="main"):
    repository = _normalize_repository(repository)
    local_roots = local_roots or {}
    diverged = []
    if repository in local_roots:
        root = Path(local_roots[repository])
        for branch in _list_remote_branches(repository, local_roots=local_roots):
            if branch == default_branch:
                continue
            tip = _git_run(["git", "-C", str(root), "rev-parse", branch]).decode().strip()
            probe = subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", tip, default_branch],
                env=_git_subprocess_env(),
                capture_output=True,
                timeout=60,
            )
            if probe.returncode != 0:
                diverged.append({"repository": repository, "branch": branch, "tip": tip})
        return diverged
    remote = _repository_remote(repository, remotes)
    with tempfile.TemporaryDirectory(prefix="vendor-branch-diff-") as directory:
        command = ["git", "-c", "credential.helper=", "-C", directory]
        def git(*args):
            result = subprocess.run(command + list(args), env=_git_subprocess_env(),
                                    capture_output=True, timeout=120)
            if result.returncode:
                raise ValueError("git branch diff failed for " + repository)
            return result.stdout
        git("init")
        git("remote", "add", "origin", remote)
        git("fetch", "--depth=1", "origin", "refs/heads/" + default_branch + ":" + default_branch)
        for branch in _list_remote_branches(repository, remotes=remotes):
            if branch == default_branch:
                continue
            try:
                git("fetch", "--depth=1", "origin", "refs/heads/" + branch + ":refs/heads/" + branch)
                tip = git("rev-parse", branch).decode().strip()
            except ValueError:
                continue
            probe = subprocess.run(
                command + ["merge-base", "--is-ancestor", tip, default_branch],
                env=_git_subprocess_env(),
                capture_output=True,
                timeout=60,
            )
            if probe.returncode != 0:
                diverged.append({"repository": repository, "branch": branch, "tip": tip})
    return diverged


def source_diff_report(registry, *, token=None, remotes=None, local_roots=None, archived=None):
    archived = archived if archived is not None else {}
    findings = []
    scan_errors = []
    for name in _locked_consumer_names(registry):
        if archived.get(name) or (token and _repository_archived(name, token)):
            continue
        try:
            findings.extend(branches_differing_from_main(
                name, remotes=remotes, local_roots=local_roots,
            ))
        except ValueError as error:
            scan_errors.append({"repository": name, "message": str(error)})
    return {
        "schema": "source-diff-report/1",
        "findings": findings,
        "finding_count": len(findings),
        "scan_errors": scan_errors,
        "scan_error_count": len(scan_errors),
    }


def _copy_parent_vendor_baseline(parent_root, work_dir):
    parent_root = Path(parent_root)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    lock = parent_root / "vendor.lock.json"
    if not lock.is_file():
        raise ValueError("missing parent vendor.lock.json")
    shutil.copy2(lock, work_dir / "vendor.lock.json")
    vendor = parent_root / "vendor"
    if vendor.is_dir():
        shutil.copytree(vendor, work_dir / "vendor", dirs_exist_ok=True)
    return work_dir


def _offline_parent_vendor_check(work_dir):
    work_dir = Path(work_dir)
    return synchronize("vendor.lock.json", work_dir, "check",
                       get=lambda url: (_ for _ in ()).throw(AssertionError("network used during parent baseline check")))


def _encode_child_heads(document):
    return (json.dumps(validate_child_heads(document), indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _open_or_update_autofix_pr(parent_repository, branch, title, body, files, token):
    parent_repository = _normalize_repository(parent_repository)
    api_repo = "https://api.github.com/repos/" + parent_repository
    base = _github_api("GET", api_repo, token)
    default_branch = base["default_branch"]
    base_sha = _github_api("GET", api_repo + "/git/ref/heads/" + quote(default_branch, safe=""), token)["object"]["sha"]
    existing = _github_api("GET", api_repo + "/pulls?state=open&head=" +
                           quote(parent_repository.split("/")[0] + ":" + branch, safe=""), token)
    pr_number = existing[0]["number"] if isinstance(existing, list) and existing else None
    try:
        _github_api("GET", api_repo + "/git/ref/heads/" + quote(branch, safe=""), token)
    except HTTPError as error:
        if error.code != 404:
            raise
        _github_api("POST", api_repo + "/git/refs", token, {
            "ref": "refs/heads/" + branch,
            "sha": base_sha,
        })
    for path, content in files.items():
        encoded = base64.b64encode(content).decode("ascii")
        get_url = api_repo + "/contents/" + quote(path, safe="/") + "?ref=" + quote(branch, safe="")
        try:
            current = _github_api("GET", get_url, token)
            file_sha = current.get("sha")
        except HTTPError as error:
            if error.code != 404:
                raise
            file_sha = None
        body_payload = {"message": "chore: refresh " + path, "content": encoded, "branch": branch}
        if file_sha:
            body_payload["sha"] = file_sha
        _github_api("PUT", api_repo + "/contents/" + quote(path, safe="/"), token, body_payload)
    if pr_number is None:
        created = _github_api("POST", api_repo + "/pulls", token, {
            "title": title,
            "head": branch,
            "base": default_branch,
            "body": body,
        })
        pr_number = created["number"]
        pr_url = created["html_url"]
    else:
        pr = _github_api("GET", api_repo + "/pulls/" + str(pr_number), token)
        pr_url = pr["html_url"]
    return {"pull_number": pr_number, "url": pr_url, "branch": branch}


def _head_sha_map(heads):
    return {name: entry["sha"] for name, entry in heads.items()}


def run_parent_child_ci(registry, document, *, parent_root=".", remotes=None, token=None,
                        autofix=False, parent_repository=PARENT_REPOSITORY, local_roots=None,
                        repo_fetch=None, skip_source_diff=False):
    document = validate_child_heads(document)
    ref = document["ref"]
    drift = []
    identity_failures = []
    live_heads = {}
    for name in _locked_consumer_names(registry):
        recorded = document["heads"].get(name)
        if recorded is None:
            raise ValueError("child-heads missing repository: " + name)
        live = fetch_child_head_record(name, ref, remotes=remotes, token=token, repo_fetch=repo_fetch)
        live_heads[name] = live
        if recorded["sha"] != live["sha"]:
            drift.append({
                "repository": name,
                "recorded": recorded["sha"],
                "live": live["sha"],
            })
        try:
            verify_reference_identity(
                expected_sha=recorded["sha"],
                expected_pull_number=None,
                expected_repository_id=recorded["repository_id"],
                resolved_sha=live["sha"],
                resolved_pull_number=None,
                resolved_repository_id=live["repository_id"],
                optional_recorded=recorded.get("optional"),
                optional_live=live.get("optional"),
            )
        except ValueError as error:
            identity_failures.append({
                "repository": name,
                "message": str(error),
            })
    desired = validate_child_heads({"schema": CHILD_HEADS_SCHEMA, "ref": ref, "heads": live_heads})
    if skip_source_diff:
        diff_report = {"schema": "source-diff-report/1", "findings": [], "finding_count": 0,
                       "scan_errors": [], "scan_error_count": 0, "skipped": True}
    else:
        diff_report = source_diff_report(registry, token=token, remotes=remotes, local_roots=local_roots)
    with tempfile.TemporaryDirectory(prefix="vendor-parent-ci-") as directory:
        work_dir = _copy_parent_vendor_baseline(parent_root, directory)
        vendor_check = _offline_parent_vendor_check(work_dir)
    autofix_result = None
    needs_heads_refresh = bool(drift)
    if autofix and needs_heads_refresh and token:
        branch = AUTOFIX_BRANCH_PREFIX
        autofix_result = _open_or_update_autofix_pr(
            parent_repository,
            branch,
            "chore: refresh child default-branch heads",
            "Automated refresh of `child-heads.json` after live `main` drift.\n\n"
            "Parent CI never merges this pull request automatically.",
            {"child-heads.json": _encode_child_heads(desired)},
            token,
        )
    failure_count = (
        len(drift) + diff_report["finding_count"] + diff_report.get("scan_error_count", 0) + len(identity_failures)
    )
    return {
        "schema": "parent-child-ci/1",
        "ref": ref,
        "drift": drift,
        "drift_count": len(drift),
        "identity_failures": identity_failures,
        "identity_failure_count": len(identity_failures),
        "source_diff": diff_report,
        "parent_vendor_check": vendor_check,
        "needs_heads_refresh": needs_heads_refresh,
        "autofix": autofix_result,
        "failure_count": failure_count,
    }


def check_child_heads(registry, document, *, remotes=None, token=None, parent_repository=PARENT_REPOSITORY,
                        local_roots=None, local_consumers=None):
    document = validate_child_heads(document)
    ref = document["ref"]
    drift = []
    live_heads = {}
    for name in _locked_consumer_names(registry):
        recorded = document["heads"].get(name)
        if recorded is None:
            raise ValueError("child-heads missing repository: " + name)
        live = fetch_child_head_record(name, ref, remotes=remotes, token=token)
        live_heads[name] = live
        if recorded["sha"] != live["sha"]:
            drift.append({
                "repository": name,
                "recorded": recorded["sha"],
                "live": live["sha"],
            })
    extra = set(document["heads"]) - set(_locked_consumer_names(registry))
    if extra:
        raise ValueError("child-heads has unknown repositories: " + ", ".join(sorted(extra)))
    pin_report = audit_registry_merged_pins(
        registry,
        parent_repository=parent_repository,
        token=token,
        remotes=remotes,
        local_roots=local_roots,
        local_consumers=local_consumers,
        consumer_revisions=_head_sha_map(document["heads"]),
    )
    pr_report = _verify_parent_pr_pins(
        registry,
        _head_sha_map(document["heads"]),
        parent_repository=parent_repository,
        token=token,
        local_consumers=local_consumers,
        pr_fetch=resolve_parent_pr,
    )
    return {
        "schema": "child-heads-check/1",
        "ref": ref,
        "drift": drift,
        "drift_count": len(drift),
        "pin_audit": pin_report,
        "parent_pr_audit": pr_report,
        "failure_count": len(drift) + pin_report["failure_count"] + pr_report["failure_count"],
    }


def resolve_parent_pr(parent_repository, pull_number, *, token=None, pr_fetch=None, reference=None,
                      repo_fetch=None):
    parent_repository = _normalize_repository(parent_repository)
    if reference is not None:
        reference = validate_parent_pr_ref(reference)
        parent_repository = reference["repository"]
        pull_number = reference["pull_number"]
    if isinstance(pull_number, str):
        if not pull_number.isdigit():
            raise ValueError("expected pull request number")
        pull_number = int(pull_number)
    if not isinstance(pull_number, int) or pull_number < 1:
        raise ValueError("expected positive pull request number")
    fetch = pr_fetch or _fetch_parent_pull_request
    pull = fetch(parent_repository, pull_number, token=token)
    merged = bool(pull.get("merged_at"))
    if merged:
        commit = pull.get("merge_commit_sha")
        if not commit:
            raise ValueError("merged pull request has no merge_commit_sha: " + str(pull_number))
        state = "merged"
    else:
        head = pull.get("head") or {}
        commit = head.get("sha")
        state = "open"
    if not commit:
        raise ValueError("cannot resolve pull request " + str(pull_number))
    _hex(commit, 40)
    repo_record = fetch_repository_record(parent_repository, token, repo_fetch=repo_fetch)
    repository_id = _coerce_repository_id(repo_record["id"])
    optional_signals = {}
    if isinstance(pull.get("created_at"), str):
        optional_signals["pull_created_at"] = pull["created_at"]
    if isinstance(repo_record.get("created_at"), str):
        optional_signals["repository_created_at"] = repo_record["created_at"]
    result = {
        "schema": PARENT_PR_RESOLVE_SCHEMA,
        "repository": parent_repository,
        "pull_number": pull_number,
        "state": state,
        "commit": commit,
        "repository_id": repository_id,
        "optional_signals": optional_signals,
    }
    if reference is not None:
        expected_sha = reference.get("commit", commit)
        verify_reference_identity(
            expected_sha=expected_sha,
            expected_pull_number=reference["pull_number"],
            expected_repository_id=reference["repository_id"],
            resolved_sha=commit,
            resolved_pull_number=pull_number,
            resolved_repository_id=repository_id,
            optional_recorded=reference.get("optional"),
            optional_live=optional_signals,
        )
    return result


def _fetch_parent_pull_request(parent_repository, pull_number, token=None):
    url = "https://api.github.com/repos/" + parent_repository + "/pulls/" + str(pull_number)
    record = _github_json(url, token)
    if not isinstance(record, dict):
        raise ValueError("invalid pull request response")
    return record


def commit_merged_into_default(repository, commit, *, remotes=None, local_roots=None):
    """Return True when commit is reachable from the source repository default branch."""
    repository = _normalize_repository(repository)
    _hex(commit, 40)
    local_roots = local_roots or {}
    if repository in local_roots:
        root = Path(local_roots[repository])
        for branch in ("main", "master"):
            probe = subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, branch],
                env=_git_subprocess_env(),
                capture_output=True,
                timeout=60,
            )
            if probe.returncode == 0:
                return True
        raise ValueError(
            "commit " + commit + " is not merged into " + repository + " default branch"
        )
    remote = _repository_remote(repository, remotes)
    with tempfile.TemporaryDirectory(prefix="vendor-merged-pin-") as directory:
        command = ["git", "-c", "credential.helper=", "-c", "core.askPass=",
                   "-c", "http.extraHeader=", "-C", directory]
        def git(*args):
            result = subprocess.run(command + list(args), env=_git_subprocess_env(),
                                    capture_output=True, timeout=120)
            if result.returncode:
                detail = result.stderr.decode("utf-8", "replace").strip()
                raise ValueError("git fetch failed for " + repository + ": " + detail)
            return result.stdout
        git("init", "--bare")
        branch = _default_branch_name(remote)
        try:
            git("fetch", "--no-tags", "--depth=1", remote, "refs/heads/" + branch + ":refs/heads/" + branch)
            git("fetch", "--no-tags", "--depth=1", remote, commit)
        except ValueError as error:
            raise ValueError("cannot fetch pin for " + repository + "@" + commit[:12] + ": " + str(error)) from error
        probe = subprocess.run(
            command + ["merge-base", "--is-ancestor", commit, branch],
            env=_git_subprocess_env(),
            capture_output=True,
            timeout=60,
        )
        if probe.returncode == 0:
            return True
        raise ValueError(
            "commit " + commit + " is not merged into " + repository + " default branch (" + branch + ")"
        )


def collect_lock_pins(lock, lock_path):
    """Return pin rows: repository, commit, entry, file."""
    if not isinstance(lock, dict) or "schema" not in lock:
        raise ValueError("invalid lock document: " + lock_path)
    schema = lock["schema"]
    pins = []
    if schema == SCHEMA:
        validated = validate(lock)
        for item in validated["files"]:
            pins.append({
                "repository": item["repository"],
                "commit": item["commit"],
                "entry": item["destination"],
                "file": lock_path,
                "kind": "vendor-lock",
            })
        return pins
    if schema == HTML_IMPORT_SCHEMA:
        sources = lock.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("html-import-vendor/1 requires nonempty sources: " + lock_path)
        for item in sources:
            if not isinstance(item, dict):
                raise ValueError("invalid html-import source in " + lock_path)
            repository = _normalize_repository(item.get("repository"))
            commit = item.get("commit")
            _hex(commit, 40)
            entry = item.get("path") or item.get("source_path") or "source"
            pins.append({
                "repository": repository,
                "commit": commit,
                "entry": entry,
                "file": lock_path,
                "kind": "html-import-vendor",
            })
        return pins
    raise ValueError("unsupported lock schema in " + lock_path + ": " + schema)


def collect_workflow_pins(workflows, *, parent_repository=PARENT_REPOSITORY):
    """Scan workflow YAML text for reusable-workflow and checkout pins to the parent."""
    if not isinstance(workflows, dict):
        raise ValueError("workflows must be a path->text mapping")
    pins = []
    uses_parent = re.compile(
        r"uses:\s*" + re.escape(parent_repository) + r"/(?P<workflow>[^\s@]+)@(?P<ref>pr/\d+|[0-9a-f]{40})\b"
    )
    checkout_repo = re.compile(r"^\s+repository:\s*" + re.escape(parent_repository) + r"\s*$")
    ref_sha = re.compile(r"^\s+ref:\s*(pr/\d+|[0-9a-f]{40})\s*$")
    for path, text in sorted(workflows.items()):
        if not isinstance(text, str):
            raise ValueError("workflow content must be text: " + path)
        lines = text.splitlines()
        for index, line in enumerate(lines, 1):
            match = uses_parent.search(line)
            if match:
                ref = match.group("ref")
                if ref.startswith("pr/"):
                    pins.append({
                        "repository": parent_repository,
                        "pull_number": int(ref.split("/", 1)[1]),
                        "entry": match.group("workflow"),
                        "file": path,
                        "line": index,
                        "kind": "workflow-uses-pr",
                    })
                else:
                    pins.append({
                        "repository": parent_repository,
                        "commit": ref,
                        "entry": match.group("workflow"),
                        "file": path,
                        "line": index,
                        "kind": "workflow-uses",
                    })
        for index, line in enumerate(lines, 1):
            if not checkout_repo.match(line):
                continue
            for offset in range(1, min(12, len(lines) - index + 1)):
                ref_match = ref_sha.match(lines[index + offset - 1])
                if ref_match:
                    ref = ref_match.group(1)
                    if ref.startswith("pr/"):
                        pins.append({
                            "repository": parent_repository,
                            "pull_number": int(ref.split("/", 1)[1]),
                            "entry": "actions/checkout",
                            "file": path,
                            "line": index + offset,
                            "kind": "workflow-checkout-pr",
                        })
                    else:
                        pins.append({
                            "repository": parent_repository,
                            "commit": ref,
                            "entry": "actions/checkout",
                            "file": path,
                            "line": index + offset,
                            "kind": "workflow-checkout",
                        })
                    break
    return pins


def audit_consumer_merged_pins(consumer, lock_documents, workflow_documents, *,
                               parent_repository=PARENT_REPOSITORY,
                               remotes=None, local_roots=None):
    """Verify every discovered pin is merged into its source repository default branch."""
    if not isinstance(consumer, str) or not consumer:
        raise ValueError("expected consumer repository")
    pins = []
    for lock_path, lock in lock_documents:
        pins.extend(collect_lock_pins(lock, lock_path))
    pins.extend(collect_workflow_pins(workflow_documents, parent_repository=parent_repository))
    checked = set()
    failures = []
    for pin in pins:
        if "pull_number" in pin:
            continue
        key = (pin["repository"], pin["commit"])
        if key in checked:
            continue
        checked.add(key)
        try:
            commit_merged_into_default(
                pin["repository"], pin["commit"], remotes=remotes, local_roots=local_roots
            )
        except ValueError as error:
            failures.append({
                "consumer": consumer,
                "file": pin["file"],
                "entry": pin.get("entry"),
                "sha": pin["commit"],
                "source_repository": pin["repository"],
                "line": pin.get("line"),
                "kind": pin.get("kind"),
                "message": str(error),
            })
    return {
        "schema": "vendor-merged-pin-audit/1",
        "consumer": consumer,
        "pins_checked": len(checked),
        "failures": failures,
    }


def _github_json(url, token=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "vendor-sync/merged-pin-audit"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("GitHub response exceeds byte limit")
    return json.loads(raw.decode("utf-8"))


def _fetch_consumer_revision(repository, ref, token=None):
    url = "https://api.github.com/repos/" + repository + "/commits/" + quote(ref, safe="")
    record = _github_json(url, token)
    if not isinstance(record, dict):
        raise ValueError("invalid commit response for " + repository)
    sha = record.get("sha")
    _hex(sha, 40)
    return sha


def _fetch_repo_text(repository, path, ref, token=None):
    url = ("https://api.github.com/repos/" + repository + "/contents/" +
           quote(path, safe="/") + "?ref=" + quote(ref, safe=""))
    try:
        data = _github_json(url, token)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        raise ValueError("invalid GitHub file response: " + repository + ":" + path)
    raw = base64.b64decode("".join(data["content"].split()), validate=True)
    if len(raw) > MAX_BYTES:
        raise ValueError("file exceeds byte limit: " + path)
    return raw.decode("utf-8")


def _fetch_workflow_documents(repository, ref, token=None):
    url = ("https://api.github.com/repos/" + repository + "/contents/.github/workflows?ref=" +
           quote(ref, safe=""))
    try:
        listing = _github_json(url, token)
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise
    if not isinstance(listing, list):
        raise ValueError("invalid workflow listing for " + repository)
    documents = {}
    for item in listing:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = item.get("name", "")
        if not name.endswith((".yml", ".yaml")):
            continue
        path = ".github/workflows/" + name
        text = _fetch_repo_text(repository, path, ref, token)
        if text is not None:
            documents[path] = text
    return documents


def _fetch_consumer_documents(consumer, lock_path, revision, token=None):
    lock_documents = []
    primary = _fetch_repo_text(consumer, lock_path, revision, token)
    if primary is None:
        raise ValueError("missing consumer lock " + consumer + ":" + lock_path)
    lock_documents.append((lock_path, json.loads(primary)))
    for candidate in HTML_IMPORT_LOCK_CANDIDATES:
        if candidate == lock_path:
            continue
        text = _fetch_repo_text(consumer, candidate, revision, token)
        if text is not None:
            lock_documents.append((candidate, json.loads(text)))
    workflows = _fetch_workflow_documents(consumer, revision, token)
    return lock_documents, workflows


def _verify_parent_pr_pins(registry, consumer_revisions, *, parent_repository=PARENT_REPOSITORY,
                           token=None, local_consumers=None, pr_fetch=None, repo_fetch=None):
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    local_consumers = local_consumers or {}
    failures = []
    checked = set()
    for entry in registry["consumers"]:
        if entry["state"] != "locked":
            continue
        name = entry["repository"]
        revision = consumer_revisions.get(name)
        if revision is None:
            continue
        if name in local_consumers:
            root = Path(local_consumers[name])
            workflow_documents = {}
            workflow_dir = root / ".github/workflows"
            if workflow_dir.is_dir():
                for item in sorted(workflow_dir.iterdir()):
                    if item.suffix in (".yml", ".yaml"):
                        workflow_documents[".github/workflows/" + item.name] = item.read_text(encoding="utf-8")
        else:
            workflow_documents = _fetch_workflow_documents(name, revision, token)
        for pin in collect_workflow_pins(workflow_documents, parent_repository=parent_repository):
            if "pull_number" not in pin:
                continue
            key = (name, pin["file"], pin["line"], pin["pull_number"])
            if key in checked:
                continue
            checked.add(key)
            try:
                parent_record = fetch_repository_record(
                    parent_repository, token, repo_fetch=repo_fetch,
                )
                resolved = resolve_parent_pr(
                    parent_repository, pin["pull_number"], token=token, pr_fetch=pr_fetch,
                    repo_fetch=repo_fetch,
                    reference={
                        "schema": PARENT_PR_REF_SCHEMA,
                        "repository": parent_repository,
                        "pull_number": pin["pull_number"],
                        "repository_id": parent_record["id"],
                    },
                )
                if resolved["state"] == "merged":
                    commit_merged_into_default(parent_repository, resolved["commit"])
            except ValueError as error:
                failures.append({
                    "consumer": name,
                    "file": pin["file"],
                    "entry": pin.get("entry"),
                    "sha": "pr/" + str(pin["pull_number"]),
                    "source_repository": parent_repository,
                    "line": pin.get("line"),
                    "kind": pin.get("kind"),
                    "message": str(error),
                })
    return {"pins_checked": len(checked), "failure_count": len(failures), "failures": failures}


def audit_registry_merged_pins(registry, *, ref="main", parent_repository=PARENT_REPOSITORY,
                               consumers=None, token=None, remotes=None, local_roots=None,
                               local_consumers=None, consumer_revisions=None):
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    selected = None if consumers is None else {name.casefold() for name in consumers}
    local_consumers = local_consumers or {}
    results = []
    failures = []
    for entry in registry["consumers"]:
        name = entry["repository"]
        if selected is not None and name.casefold() not in selected:
            continue
        if entry["state"] != "locked":
            results.append({"consumer": name, "state": entry["state"], "skipped": True})
            continue
        if name in local_consumers:
            root = Path(local_consumers[name])
            if consumer_revisions and name in consumer_revisions:
                revision = consumer_revisions[name]
                _hex(revision, 40)
            else:
                revision = _git_run(
                    ["git", "-C", str(root), "rev-parse", ref],
                    timeout=60,
                ).decode().strip()
                _hex(revision, 40)
            lock_documents = []
            for lock_path in [entry["lock"], *HTML_IMPORT_LOCK_CANDIDATES]:
                path = root / lock_path
                if path.is_file():
                    lock_documents.append((lock_path, json.loads(path.read_text(encoding="utf-8"))))
            workflow_documents = {}
            workflow_dir = root / ".github/workflows"
            if workflow_dir.is_dir():
                for item in sorted(workflow_dir.iterdir()):
                    if item.suffix in (".yml", ".yaml"):
                        workflow_documents[".github/workflows/" + item.name] = item.read_text(encoding="utf-8")
        else:
            if consumer_revisions and name in consumer_revisions:
                revision = consumer_revisions[name]
                _hex(revision, 40)
            else:
                revision = _fetch_consumer_revision(name, ref, token)
            lock_documents, workflow_documents = _fetch_consumer_documents(
                name, entry["lock"], revision, token
            )
        report = audit_consumer_merged_pins(
            name, lock_documents, workflow_documents,
            parent_repository=parent_repository, remotes=remotes, local_roots=local_roots,
        )
        report["revision"] = revision
        results.append(report)
        failures.extend(report["failures"])
    return {
        "schema": "vendor-merged-pin-audit-report/1",
        "ref": ref,
        "parent_repository": parent_repository,
        "results": results,
        "failure_count": len(failures),
        "failures": failures,
    }


def _write_audit_summary(report, summary_path):
    lines = ["## Vendor merged-pin audit", ""]
    if report["failure_count"]:
        lines.append(f"**{report['failure_count']} unmerged pin(s) found.**")
        lines.append("")
        lines.append("| Consumer | File | Entry | SHA | Source repository |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in report["failures"]:
            line = "" if row.get("line") is None else str(row["line"])
            file_cell = row["file"] + (":" + line if line else "")
            lines.append("| {consumer} | {file} | {entry} | `{sha}` | {source} |".format(
                consumer=row["consumer"],
                file=file_cell,
                entry=row.get("entry") or "",
                sha=row["sha"],
                source=row["source_repository"],
            ))
    else:
        lines.append("All checked pins are merged into their source default branches.")
    Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_child_heads_summary(report, summary_path):
    lines = ["## Parent child CI", ""]
    if report.get("schema") == "parent-child-ci/1":
        if report["drift_count"]:
            lines.append(f"**{report['drift_count']} child head(s) drifted from child-heads.json.**")
            if report.get("autofix"):
                lines.append(f"Auto-fix PR: {report['autofix']['url']}")
            lines.append("")
            lines.append("| Repository | Recorded | Live |")
            lines.append("| --- | --- | --- |")
            for row in report["drift"]:
                lines.append("| {repo} | `{recorded}` | `{live}` |".format(
                    repo=row["repository"], recorded=row["recorded"], live=row["live"],
                ))
            lines.append("")
        if report.get("identity_failure_count"):
            lines.append(f"**{report['identity_failure_count']} identity mismatch(es).**")
            for row in report["identity_failures"]:
                lines.append(f"- `{row['repository']}`: {row['message']}")
            lines.append("")
        if report["source_diff"]["finding_count"]:
            lines.append(f"**{report['source_diff']['finding_count']} branch tip(s) differ from default branch.**")
            lines.append("")
            for row in report["source_diff"]["findings"]:
                lines.append(f"- `{row['repository']}` branch `{row['branch']}` @ `{row['tip']}`")
            lines.append("")
        if not report["failure_count"]:
            lines.append("Child heads match, source branches are clean, and parent vendor baseline check passed.")
        Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines = ["## Child head and pin audit", ""]
    if report["drift_count"]:
        lines.append(f"**{report['drift_count']} child head(s) drifted from child-heads.json.**")
        lines.append("")
    failures = report.get("pin_audit", {}).get("failures", []) + report.get("parent_pr_audit", {}).get("failures", [])
    if failures:
        lines.append(f"**{len(failures)} pin resolution failure(s).**")
    if not report["failure_count"]:
        lines.append("Checks passed.")
    Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def promote(manifest, root, *, get=_get):
    """Explicitly promote a verified upstream candidate into the working tree.

    This never commits, pushes, opens a PR, or mutates GitHub. It requires the
    current locked bytes to be intact, reuses the canonical update resolver,
    verifies the resulting baseline offline, and returns a deterministic
    identity receipt suitable for review/CI evidence.
    """
    root = Path(root).resolve()
    manifest_path = _target(root, manifest)
    before = validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    before_by_destination = {item["destination"]: copy.deepcopy(item) for item in before["files"]}
    backups = {manifest_path: manifest_path.read_bytes()}
    for item in before["files"]:
        target = _target(root, item["destination"])
        backups[target] = target.read_bytes()
    try:
        result = synchronize(manifest, root, "update", get=get)
        synchronize(manifest, root, "check", get=lambda url: (_ for _ in ()).throw(AssertionError("network used during promoted baseline check")))
    except BaseException:
        for target, data in backups.items():
            _atomic(target, data)
        raise
    after = validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    changes = []
    for item in after["files"]:
        old = before_by_destination[item["destination"]]
        if old != item:
            changes.append({
                "repository": item["repository"],
                "source": item["source"],
                "destination": item["destination"],
                "old_commit": old["commit"],
                "new_commit": item["commit"],
                "old_blob_sha": old["blob_sha"],
                "new_blob_sha": item["blob_sha"],
                "old_sha256": old["sha256"],
                "new_sha256": item["sha256"],
            })
    return {
        "schema": "vendor-promotion/1",
        "mode": "promote",
        "changed_paths": result["changed_paths"],
        "promoted": changes,
    }


def main(argv=None, *, get=_get):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "check",
            "materialize",
            "update",
            "promote",
            "enroll",
            "evidence",
            "audit-merged-pins",
            "refresh-child-heads",
            "check-child-heads",
            "run-parent-child-ci",
            "resolve-parent-pr",
        ),
    )
    parser.add_argument("--manifest", default="vendor.lock.json")
    parser.add_argument("--root", default=".")
    parser.add_argument("--runtime-evidence", action="append", default=[])
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--ref", default="main", help="consumer default branch ref to audit")
    parser.add_argument("--parent", default=PARENT_REPOSITORY)
    parser.add_argument("--consumer", action="append", default=[], help="limit audit to selected consumers")
    parser.add_argument("--summary", help="write a GitHub Actions step summary markdown file")
    parser.add_argument("--local-consumer", action="append", default=[], metavar="OWNER/REPO=PATH",
                        help="offline consumer checkout root (repeatable)")
    parser.add_argument("--heads", default="child-heads.json")
    parser.add_argument("--pr", help="parent pull request number for resolve-parent-pr")
    parser.add_argument("--ref-file", default="templates/parent-pr.ref.json",
                        help="parent-pr-ref/1 JSON for resolve-parent-pr verification")
    parser.add_argument("--autofix", action="store_true",
                        help="open or update a parent pull request with refreshed child-heads.json")
    parser.add_argument("--skip-source-diff", action="store_true",
                        help="skip cross-repo branch diff scan (pull request validation only)")
    args = parser.parse_args(argv)
    try:
        if args.mode == "run-parent-child-ci":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            document = json.loads(Path(args.heads).read_text(encoding="utf-8"))
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            result = run_parent_child_ci(
                registry,
                document,
                parent_root=args.root,
                token=token,
                autofix=args.autofix,
                parent_repository=args.parent,
                skip_source_diff=args.skip_source_diff,
            )
            if args.summary:
                _write_child_heads_summary(result, args.summary)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["failure_count"] else 0
        if args.mode == "refresh-child-heads":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            result = refresh_child_heads(registry, args.heads, ref=args.ref, token=token)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.mode == "check-child-heads":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            document = json.loads(Path(args.heads).read_text(encoding="utf-8"))
            local_consumers = {}
            for value in args.local_consumer:
                name, sep, root = value.partition("=")
                if not sep or not name or not root:
                    raise ValueError("expected --local-consumer OWNER/REPO=PATH")
                local_consumers[_normalize_repository(name)] = str(Path(root).resolve())
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            result = check_child_heads(
                registry,
                document,
                token=token,
                parent_repository=args.parent,
                local_consumers=local_consumers or None,
            )
            if args.summary:
                _write_child_heads_summary(result, args.summary)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["failure_count"] else 0
        if args.mode == "resolve-parent-pr":
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            reference = None
            ref_path = Path(args.ref_file)
            if ref_path.is_file():
                reference = json.loads(ref_path.read_text(encoding="utf-8"))
                reference = validate_parent_pr_ref(reference)
            if args.pr:
                pull_number = int(args.pr)
            elif reference is not None:
                pull_number = reference["pull_number"]
            else:
                raise ValueError("--pr or --ref-file with pull_number is required for resolve-parent-pr")
            result = resolve_parent_pr(
                args.parent,
                pull_number,
                token=token,
                reference=reference,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.mode == "audit-merged-pins":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            local_consumers = {}
            for value in args.local_consumer:
                name, sep, root = value.partition("=")
                if not sep or not name or not root:
                    raise ValueError("expected --local-consumer OWNER/REPO=PATH")
                local_consumers[_normalize_repository(name)] = str(Path(root).resolve())
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            result = audit_registry_merged_pins(
                registry,
                ref=args.ref,
                parent_repository=args.parent,
                consumers=args.consumer or None,
                token=token,
                local_consumers=local_consumers or None,
            )
            if args.summary:
                _write_audit_summary(result, args.summary)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["failure_count"] else 0
        if args.mode == "promote":
            result = promote(args.manifest, args.root, get=get)
        elif args.mode == "evidence":
            result = evidence(args.manifest, args.root, runtime=args.runtime_evidence)
        else:
            result = synchronize(args.manifest, args.root, args.mode, get=get)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, HTTPError) as error:
        print("vendor-sync: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
