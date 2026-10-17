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
from urllib.error import HTTPError, URLError
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
    "list_org_repositories",
    "record_is_public_monitored",
    "record_is_public_source_pin",
    "monitored_repositories",
    "run_child_ci",
    "plan_heads_refresh",
    "verify_child_lock_against_parent",
    "verify_workflow_template_copy",
    "main",
]

PARENT_PR_REF_SCHEMA = "parent-pr-ref/1"
AUTOFIX_BRANCH_PREFIX = "vendor/child-heads-sync"

SCHEMA = "vendor-lock/1"
HTML_IMPORT_SCHEMA = "html-import-vendor/1"
CHILD_HEADS_SCHEMA = "child-heads/1"
PARENT_PR_RESOLVE_SCHEMA = "parent-pr-resolve/1"
PARENT_REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
ORG_OWNER = "myon-bioinformatics"
CHILD_CI_TEMPLATE = "templates/vendor-child-ci.workflow.yml"
CHILD_CI_WORKFLOW_PATH = ".github/workflows/vendor-child-ci.yml"
PUBLIC_GIT_FETCH_ERROR = "public Git fetch/read failed"
PUBLIC_ACCESS_BLOCKED = "GitHub public access blocked (authentication or rate limit)"
PIN_NOT_MERGED_ERROR = "pin commit is not merged into source default branch"
_COMMIT_ATOM_SHA = re.compile(r"Commit/([0-9a-f]{40})")
_REPOSITORY_ID_META = re.compile(
    r'<meta name="octolytics-dimension-repository_id" content="(\d+)"'
)
_REPOSITORY_PUBLIC_META = re.compile(
    r'<meta name="octolytics-dimension-repository_public" content="(true|false)"'
)
_REPOSITORY_NWO_META = re.compile(
    r'<meta name="octolytics-dimension-repository_nwo" content="([^"]+)"'
)
BRANCH_DIFF_SCAN_ERROR = "branch diff scan failed"
AUDIT_SOURCE_ERROR = "source verification failed"
HTML_IMPORT_LOCK_CANDIDATES = ("tools/html-import/vendor.lock.json",)
MAX_BYTES = 8 * 1024 * 1024
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _path(value):
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or ":" in value:
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


class PublicGitNotFound(ValueError):
    """Public GitHub URL returned 404 (skip without naming private repositories)."""


class PublicGitAccessBlocked(ValueError):
    """Public GitHub URL returned 401/403 (authentication or rate limit)."""


def _public_http_get(url):
    headers = {"User-Agent": "vendor-sync/public-github"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise PublicGitNotFound("not found") from error
        if error.code in (401, 403):
            raise PublicGitAccessBlocked(PUBLIC_ACCESS_BLOCKED) from error
        raise ValueError(PUBLIC_GIT_FETCH_ERROR) from error
    except URLError as error:
        raise ValueError(PUBLIC_GIT_FETCH_ERROR) from error
    if len(raw) > MAX_BYTES:
        raise ValueError("download exceeds byte limit")
    return raw


def public_ref_sha(repository, ref="main"):
    """Resolve a moving branch ref to an immutable commit SHA via the public Atom feed."""
    repository = _normalize_repository(repository)
    if not isinstance(ref, str) or not ref or any(ord(c) < 32 for c in ref):
        raise ValueError("invalid ref")
    url = "https://github.com/{}/commits/{}.atom".format(repository, quote(ref, safe=""))
    match = _COMMIT_ATOM_SHA.search(_public_http_get(url).decode("utf-8", "replace"))
    if not match:
        raise ValueError("missing commit in atom feed")
    sha = match.group(1)
    _hex(sha, 40)
    return sha


def _public_repo_default_branch(repository):
    repository = _normalize_repository(repository)
    url = "https://api.github.com/repos/{}".format(repository)
    payload = json.loads(_public_http_get(url).decode("utf-8"))
    if not record_is_public_source_pin(payload):
        raise ValueError("source repository is not public")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ValueError(PUBLIC_GIT_FETCH_ERROR)
    return branch


def public_commit_exists(repository, commit, *, entry_label=None):
    """Fail closed unless the pin commit is reachable from the public default branch.

    Uses the compare API (``default...commit``). Only ``behind`` and ``identical``
    mean the pin is delivered from the default branch; ``ahead``/``diverged`` are
    rejected even when the commit object exists on a side branch. ``404`` is a
    missing pin; ``401``/``403`` raise :class:`PublicGitAccessBlocked`.
    """
    repository = _normalize_repository(repository)
    _hex(commit, 40)
    label = entry_label if isinstance(entry_label, str) and entry_label else commit[:7]
    try:
        branch = _public_repo_default_branch(repository)
        url = "https://api.github.com/repos/{}/compare/{}...{}".format(
            repository, quote(branch, safe=""), commit,
        )
        payload = json.loads(_public_http_get(url).decode("utf-8"))
    except PublicGitNotFound as error:
        raise ValueError("catalog pin commit not found: " + label) from error
    status = payload.get("status")
    if status not in ("behind", "identical"):
        raise ValueError("catalog pin commit not reachable from default branch: " + label)
    return commit


def public_raw_bytes(repository, ref, path):
    """Fetch file bytes from raw.githubusercontent.com; None when the path is absent (404)."""
    repository = _normalize_repository(repository)
    _path(path)
    _hex(ref, 40)
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        repository, ref, quote(path, safe="/"),
    )
    try:
        return _public_http_get(url)
    except PublicGitNotFound:
        return None


def _public_repo_is_archived_from_html(html):
    return ("This repository has been archived" in html
            or '"isArchived":true' in html)


def public_repository_page_record(repository):
    """Read repository identity from the public github.com HTML page (no REST API).

    Uses ``octolytics-dimension-repository_id`` (and related meta tags) from the
    same response used for archived detection. Atom/raw URLs are not used for id.
    """
    repository = _normalize_repository(repository)
    html = _public_http_get("https://github.com/" + repository).decode("utf-8", "replace")
    id_match = _REPOSITORY_ID_META.search(html)
    if not id_match:
        raise ValueError("missing repository id in public page metadata")
    public_match = _REPOSITORY_PUBLIC_META.search(html)
    is_public = public_match is not None and public_match.group(1) == "true"
    nwo_match = _REPOSITORY_NWO_META.search(html)
    full_name = nwo_match.group(1) if nwo_match else repository
    if full_name.casefold() != repository.casefold():
        raise ValueError("repository name mismatch on public page")
    return {
        "id": int(id_match.group(1)),
        "full_name": full_name,
        "private": not is_public,
        "visibility": "public" if is_public else "private",
        "archived": _public_repo_is_archived_from_html(html),
    }


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
                raise ValueError(PUBLIC_GIT_FETCH_ERROR)
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


# --- Generic GitHub identity lookups are delegated to the vendored gh_identity
# (vendor/gh_identity.py, pinned in vendor.lock.json). Vendor-specific policy stays here.
GH_IDENTITY_PATH = Path(__file__).resolve().parent / "vendor" / "gh_identity.py"
_GH_IDENTITY = None


def _gh_identity():
    """Load the vendored gh_identity by path (stdlib only; works under python -S)."""
    global _GH_IDENTITY
    if _GH_IDENTITY is None:
        import importlib.util
        name = "_vendored_gh_identity"
        spec = importlib.util.spec_from_file_location(name, GH_IDENTITY_PATH)
        if spec is None or spec.loader is None:
            raise ValueError("vendored gh_identity is missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        _GH_IDENTITY = module
    return _GH_IDENTITY


def _ghi_call(function, *args, fetch, max_pages=100, timeout=300, **kwargs):
    """Run a read-only gh_identity function with HTTP routed through `fetch(url)`.

    Keeps this module's transport (token only in headers, byte limit, test
    monkeypatching of _github_api/_github_json) while gh_identity owns the logic.
    """
    ghi = _gh_identity()

    def handler(method, path, **_ignored):
        if method != "GET":
            raise ValueError("gh_identity wrapper is read-only")
        return fetch("https://api.github.com/" + path)

    with ghi.operation(max_pages=max_pages, max_items=100000, max_bytes=100000000, timeout=timeout):
        with ghi.request_stub(handler):
            return function(*args, **kwargs)


_GITHUB_TOKEN_PATTERN = re.compile(
    r"\b(?:ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gho_[A-Za-z0-9_]{20,}|ghs_[A-Za-z0-9_]{20,}|"
    r"ghu_[A-Za-z0-9_]{20,}|ghr_[A-Za-z0-9_]{20,})\b"
)


def _git_http_config_args():
    return ["-c", "credential.helper=", "-c", "core.askPass=", "-c", "http.extraHeader="]


def _redact_secrets(message, token=None):
    if not isinstance(message, str):
        return message
    if token:
        message = message.replace(token, "***")
    message = re.sub(r"https://x-access-token:[^@]+@", "https://***@", message)
    message = re.sub(
        r"[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn]: [Bb]earer \S+",
        "AUTHORIZATION: bearer ***",
        message,
    )
    message = re.sub(r"\b[Bb]earer\s+\S+", "Bearer ***", message)
    message = _GITHUB_TOKEN_PATTERN.sub("***", message)
    return message


def _git_run(argv, *, timeout=120):
    result = subprocess.run(argv, env=_git_subprocess_env(), capture_output=True, timeout=timeout)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or result.stdout.decode("utf-8", "replace").strip()
        raise ValueError("git failed: " + _redact_secrets(detail))
    return result.stdout


def _default_branch_name(remote):
    argv = ["git"] + _git_http_config_args() + ["ls-remote", "--symref", remote, "HEAD"]
    result = subprocess.run(argv, env=_git_subprocess_env(), capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(PUBLIC_GIT_FETCH_ERROR)
    output = result.stdout.decode("utf-8", "replace")
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.split("refs/heads/", 1)[1].split("\t", 1)[0]
    raise ValueError(PUBLIC_GIT_FETCH_ERROR)


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


def record_is_public_source_pin(record):
    """True when a vendored source repository is public (archived sources are allowed)."""
    if not isinstance(record, dict):
        return False
    if record.get("private") is not False:
        return False
    if record.get("visibility") != "public":
        return False
    full_name = record.get("full_name")
    if not isinstance(full_name, str) or not full_name:
        return False
    return True


def record_is_public_monitored(record):
    """True when a GitHub repository record is tracked in child-heads.json."""
    if not isinstance(record, dict):
        return False
    if record.get("archived"):
        return False
    return record_is_public_source_pin(record)


def monitored_repositories(registry, *, token=None, owner=ORG_OWNER):
    """Locked parent-registry consumers that are public and not archived."""
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    monitored = []
    for entry in registry["consumers"]:
        if entry["state"] != "locked":
            continue
        name = entry["repository"]
        if name.split("/")[0].casefold() != owner.casefold():
            continue
        if token:
            record = fetch_repository_record(name, token)
        else:
            try:
                record = public_repository_page_record(name)
            except PublicGitNotFound:
                raise ValueError(
                    "registered consumer repository is not publicly accessible: " + name,
                ) from None
            except PublicGitAccessBlocked:
                raise
        if not record_is_public_monitored(record):
            raise ValueError(
                "registered consumer is not a public non-archived repository: " + name,
            )
        monitored.append(name)
    return sorted(set(monitored), key=str.casefold)


def _api_token(token=None):
    if token:
        return token
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def _split_extra_heads(head_names, monitored, *, token=None, repo_fetch=None):
    """Classify child-heads keys outside the monitored set without leaking private names."""
    extra = sorted(set(head_names) - set(monitored), key=str.casefold)
    unexpected = []
    excluded_count = 0
    for name in extra:
        try:
            record = fetch_repository_record(name, token, repo_fetch=repo_fetch)
        except PublicGitAccessBlocked:
            raise
        except HTTPError as error:
            if error.code in (403, 404):
                excluded_count += 1
                continue
            raise
        except (PublicGitNotFound, ValueError):
            excluded_count += 1
            continue
        if record_is_public_monitored(record):
            unexpected.append(name)
        else:
            excluded_count += 1
    return unexpected, excluded_count


def _list_account_repositories(owner, *, token=None):
    """Public, non-archived repositories of an org (falling back to a user account).

    Thin wrapper over gh_identity.list_owner_repositories; incomplete listings are errors.
    """
    token = _api_token(token)
    ghi = _gh_identity()

    def fetch(url):
        return _github_api("GET", url, token) if token else _github_json(url, None)

    names = []
    for kind in ("org", "user"):
        try:
            result = _ghi_call(ghi.list_owner_repositories, owner, kind=kind, include_forks=True,
                               max_pages=20, fetch=fetch)
        except HTTPError as error:
            if error.code == 404 and kind == "org":
                continue
            raise
        except ghi.Error as error:
            raise ValueError("invalid account repository listing: " + error.code) from None
        if not result["complete"]:
            raise ValueError("account repository listing truncated")
        names = []
        for record in result["repositories"]:
            name = _normalize_repository(record["full_name"])
            if name.split("/")[0].casefold() == owner.casefold():
                names.append(name)
        if names:
            break
    return sorted(set(names), key=str.casefold)


def list_org_repositories(owner=ORG_OWNER, *, token=None):
    return _list_account_repositories(owner, token=token)


def verify_workflow_template_copy(copied_text, template_text, *, path=CHILD_CI_WORKFLOW_PATH):
    if copied_text != template_text:
        raise ValueError(path + " must match parent template bytes exactly")
    scan = copied_text.decode("utf-8") if isinstance(copied_text, bytes) else copied_text
    if "@pr/" in scan or "refs/pull/" in scan:
        raise ValueError(path + " must not use @pr/ or refs/pull/ pins")
    return True


def _lookup_child_main_pull_number(repository, sha, token=None):
    """First pull request associated with sha (gh_identity.commit_pulls), or None."""
    token = _api_token(token)
    if not token:
        return None
    ghi = _gh_identity()
    try:
        result = _ghi_call(ghi.commit_pulls, _normalize_repository(repository), sha,
                           fetch=lambda url: _github_json(url, token))
    except (HTTPError, ghi.Error):
        return None
    if result["pulls"]:
        return result["pulls"][0].get("number")
    return None


def _ensure_public_source_repository(repository, *, token=None, repo_fetch=None):
    record = fetch_repository_record(repository, token, repo_fetch=repo_fetch)
    if not record_is_public_source_pin(record):
        raise ValueError("source repository is not public")
    return record


def verify_child_lock_against_parent(registry, catalog, lock, consumer):
    """Validate a child lock against parent registry/catalog without child-heads."""
    import vendor_catalog
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    catalog = vendor_catalog.validate(catalog)
    lock = validate(lock)
    if not vendor_consumers.compare(registry, lock, consumer)["matches"]:
        raise ValueError("consumer topology mismatch against parent registry")
    if any(item["ref"] != item["commit"] for item in lock["files"]):
        raise ValueError("child lock requires ref=commit SHA pins")
    # Registry topology is the parent-approved adopted contract. Catalog
    # enrollment only controls future recommendations, not existing copies.
    return lock


def _audit_failure_message(error, token=None):
    message = _redact_secrets(str(error), token=_api_token(token))
    if message in (
        PUBLIC_GIT_FETCH_ERROR,
        PIN_NOT_MERGED_ERROR,
        "source repository is not public",
        "consumer topology mismatch against parent registry",
        "catalog skips vendored source for this consumer",
        "child lock requires ref=commit SHA pins",
        "Git snapshot SHA mismatch",
    ):
        return message
    if "blob mismatch" in message or "mismatch" in message or "topology" in message:
        return AUDIT_SOURCE_ERROR
    if "git" in message.casefold() or "fetch" in message.casefold() or "not found" in message.casefold():
        return PUBLIC_GIT_FETCH_ERROR
    if "merged" in message.casefold():
        return PIN_NOT_MERGED_ERROR
    return AUDIT_SOURCE_ERROR


def build_expected_child_lock(registry, document, consumer, *, token=None):
    """Load adopted pins from the immutable child revision recorded by the parent.

    The catalog is a recommendation, not the adopted lock. Retaining the existing
    vendor-lock/1 also covers LICENSE/helper rows absent from the catalog.
    """
    import vendor_consumers
    entry = vendor_consumers.get_consumer(registry, consumer)
    if entry["state"] != "locked":
        raise ValueError("child CI requires a locked consumer registration")
    heads = validate_child_heads(document)["heads"]
    recorded = next((v for k, v in heads.items() if k.casefold() == consumer.casefold()), None)
    if recorded is None:
        raise ValueError("child-heads missing repository: " + consumer)
    meta = _live_consumer_repository_record(consumer, token=token)
    if not record_is_public_monitored(meta):
        raise ValueError("child is not a public non-archived repository")
    if _coerce_repository_id(meta["id"]) != recorded["repository_id"]:
        raise ValueError("child repository id mismatch")
    text = _fetch_repo_text(consumer, entry["lock"], recorded["sha"], token)
    if text is None:
        raise ValueError("missing adopted child lock at recorded SHA")
    lock = validate(json.loads(text))
    if any(item["ref"] != item["commit"] for item in lock["files"]):
        raise ValueError("adopted child lock requires ref=commit SHA")
    if not vendor_consumers.compare(registry, lock, consumer)["matches"]:
        raise ValueError("consumer topology mismatch against parent registry")
    return lock


def audit_adopted_child_files(registry, document, repository_names, *, token=None,
                             parent_root=None, parent_repository=PARENT_REPOSITORY,
                             remotes=None, local_roots=None):
    """Independently check adopted source objects and child bytes; execute neither.

    A child lock is a claim, not evidence for its own digests. Read regular Git
    blobs at the claimed source SHA before comparing the child's placed files.
    Cache immutable snapshots within this run, never across baseline changes.
    """
    import vendor_consumers
    registry = vendor_consumers.validate(registry)
    selected = set(repository_names)
    failures, checked, snapshots = [], [], {}

    def snapshot(repository, revision, paths):
        paths = tuple(sorted(set(paths)))
        key = (repository, revision, paths)
        if key not in snapshots:
            resolved, files = _public_git_snapshot(repository, revision, paths)
            if resolved != revision:
                raise ValueError("Git snapshot SHA mismatch")
            snapshots[key] = files
        return snapshots[key]

    for entry in registry["consumers"]:
        name = entry["repository"]
        if name not in selected or entry["state"] != "locked":
            continue
        try:
            local_parent = parent_root is not None and name == parent_repository
            if local_parent:
                # The parent has an existing candidate-update lock contract:
                # ref may be a branch, but commit/blob/digest remain immutable.
                # Check this PR's parent bytes, not the old main snapshot.
                root = Path(parent_root).resolve()
                lock = validate(json.loads(_target(root, entry["lock"]).read_text(encoding="utf-8")))
                if not vendor_consumers.compare(registry, lock, name)["matches"]:
                    raise ValueError("parent topology mismatch")
            else:
                lock = build_expected_child_lock(registry, document, name, token=token)
            groups = {}
            for item in lock["files"]:
                groups.setdefault((item["repository"], item["commit"]), []).append(item)
            for (repository, revision), items in groups.items():
                _ensure_public_source_repository(repository, token=token)
                commit_merged_into_default(
                    repository,
                    revision,
                    remotes=remotes,
                    local_roots=local_roots,
                    token=_api_token(token),
                )
                sources = snapshot(repository, revision, [item["source"] for item in items])
                for item in items:
                    blob, data = sources[item["source"]]
                    if blob != item["blob_sha"]:
                        raise ValueError("source blob mismatch: " + item["destination"])
                    _verify(item, data)
            if local_parent:
                files = {item["destination"]: (None, _target(root, item["destination"]).read_bytes())
                         for item in lock["files"]}
            else:
                files = snapshot(name, document["heads"][name]["sha"],
                                 [item["destination"] for item in lock["files"]])
            for item in lock["files"]:
                _verify(item, files[item["destination"]][1])
            checked.append({"repository": name, "files_checked": len(lock["files"])})
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            failures.append({
                "repository": name,
                "message": _audit_failure_message(error, token=token),
            })
    return {"checked": checked, "failures": failures, "failure_count": len(failures)}


def _verify_locked_ci(name, revision, contract):
    """Read approved workflow bytes from a regular Git blob; never execute them."""
    path = contract["workflow"]
    resolved, files = _public_git_snapshot(name, revision, [path])
    if resolved != revision:
        raise ValueError("locked CI snapshot SHA mismatch")
    if hashlib.sha256(files[path][1]).hexdigest() != contract["sha256"]:
        raise ValueError("locked CI workflow differs from parent-approved bytes")
    return {"repository": name, "workflow": path, "sha256": contract["sha256"]}


def _workflow_refresh_block_reason(name, old_sha, new_sha, template_text, *, token=None,
                                   locked_ci=None):
    if locked_ci is not None:
        try:
            _verify_locked_ci(name, new_sha, locked_ci)
        except (ValueError, OSError, subprocess.SubprocessError):
            return "locked_ci_mismatch"
    old_text = _fetch_repo_text(name, CHILD_CI_WORKFLOW_PATH, old_sha, _api_token(token))
    new_text = _fetch_repo_text(name, CHILD_CI_WORKFLOW_PATH, new_sha, _api_token(token))
    if old_text == new_text:
        return None
    if new_text is None:
        # Child repos may drop vendor-child-ci.yml; do not block heads refresh.
        return None
    try:
        verify_workflow_template_copy(new_text, template_text)
    except ValueError:
        return "workflow_template_mismatch"
    return None


def plan_heads_refresh(registry, document, proposed_heads, template_text, *, token=None,
                       audit_blocked=None):
    """Merge proposed main tips into child-heads, skipping repos that need parent review."""
    document = validate_child_heads(document)
    merged_heads = {
        name: document["heads"][name]
        for name in proposed_heads
        if name in document["heads"]
    }
    skipped = []
    blocked = {name.casefold() for name in (audit_blocked or [])}
    for name, new in proposed_heads.items():
        old = document["heads"].get(name)
        if name.casefold() in blocked:
            skipped.append({"repository": name, "reason": "adopted_baseline_audit_failure"})
            if old is not None:
                merged_heads[name] = old
            continue
        if old is None:
            skipped.append({"repository": name, "reason": "new_monitored_repository"})
            continue
        if _coerce_repository_id(old["repository_id"]) != _coerce_repository_id(new["repository_id"]):
            skipped.append({"repository": name, "reason": "repository_id_mismatch"})
            continue
        if old["sha"] == new["sha"]:
            merged_heads[name] = new
            continue
        reason = _workflow_refresh_block_reason(
            name, old["sha"], new["sha"], template_text, token=token,
            locked_ci=next((row.get("locked_ci") for row in registry["consumers"]
                            if row["repository"].casefold() == name.casefold()), None),
        )
        if reason is not None:
            skipped.append({"repository": name, "reason": reason})
            continue
        merged_heads[name] = new
    return {"heads": merged_heads, "skipped": skipped}


def _baseline_audit_blocked(registry, document, repository_names, template_text, *,
                            token=None, parent_root=None, parent_repository=PARENT_REPOSITORY,
                            remotes=None, local_roots=None):
    """Return repositories whose baseline audit fails at the SHAs in document."""
    names = list(repository_names)
    template_report = audit_child_workflow_templates(
        registry, document, template_text, token=token, repository_names=names,
    )
    child_files = audit_adopted_child_files(
        registry, document, names, token=token,
        parent_root=parent_root, parent_repository=parent_repository,
        remotes=remotes, local_roots=local_roots,
    )
    blocked = sorted({
        row["repository"] for row in template_report["failures"] + child_files["failures"]
    }, key=str.casefold)
    return blocked, template_report, child_files


def run_child_ci(root, parent_ref_path, consumer, *, token=None,
                 parent_repository=PARENT_REPOSITORY, get=_get, parent_root=None):
    """Check a child against the adopted lock anchored in the executed parent pin."""
    import vendor_consumers
    root = Path(root).resolve()
    consumer = _normalize_repository(consumer)
    reference = validate_parent_pr_ref(json.loads(Path(parent_ref_path).read_text(encoding="utf-8")))
    if reference["repository"].casefold() != _normalize_repository(parent_repository).casefold():
        raise ValueError("parent-pr.ref.json repository mismatch")
    parent_sha = reference.get("commit")
    if parent_sha is None:
        raise ValueError("child CI requires an explicit parent commit SHA")
    parent_root = Path(parent_root or Path(__file__).resolve().parent).resolve()
    actual_sha = _git_run(["git", "-C", str(parent_root), "rev-parse", "HEAD"]).decode().strip()
    if actual_sha != parent_sha:
        raise ValueError("executed parent SHA mismatch")
    # The workflow verifies PR/repository identity before running this code.
    # Never resolve a mutable open PR a second time or read data from another SHA.
    registry = json.loads((parent_root / "vendor-consumers.json").read_text(encoding="utf-8"))
    catalog = json.loads((parent_root / "vendor-catalog.json").read_text(encoding="utf-8"))
    template = (parent_root / CHILD_CI_TEMPLATE).read_bytes()
    copied_path = _target(root, CHILD_CI_WORKFLOW_PATH)
    verify_workflow_template_copy(copied_path.read_bytes(), template)
    entry = vendor_consumers.get_consumer(registry, consumer)
    local_lock = validate(json.loads(_target(root, entry["lock"]).read_text(encoding="utf-8")))
    verify_child_lock_against_parent(registry, catalog, local_lock, consumer)
    failures = []
    groups = {}
    for item in local_lock["files"]:
        groups.setdefault((item["repository"], item["commit"]), []).append(item)
    api_token = _api_token(token)
    for (repository, revision), items in groups.items():
        try:
            _ensure_public_source_repository(repository, token=api_token)
            commit_merged_into_default(repository, revision, token=api_token)
            resolved, sources = _public_git_snapshot(repository, revision, [row["source"] for row in items])
            if resolved != revision:
                raise ValueError("Git snapshot SHA mismatch")
            for item in items:
                blob, source_data = sources[item["source"]]
                if blob != item["blob_sha"]:
                    raise ValueError("source blob mismatch: " + item["destination"])
                _verify(item, source_data)
                _verify(item, _target(root, item["destination"]).read_bytes())
        except (OSError, ValueError) as error:
            for item in items:
                failures.append({
                    "destination": item["destination"],
                    "message": _audit_failure_message(error, token=api_token),
                })
    if failures:
        raise ValueError(
            "child vendor file mismatch: "
            + _redact_secrets(json.dumps(failures, ensure_ascii=False), token=api_token)
        )
    return {
        "schema": "child-ci/1",
        "consumer": consumer,
        "parent_commit": parent_sha,
        "files_checked": len(local_lock["files"]),
        "resolved_pull_number": reference["pull_number"],
        "resolved_repository_id": reference["repository_id"],
    }


def audit_child_workflow_templates(registry, document, template_text, *, token=None,
                                 repository_names=None):
    import vendor_consumers
    failures = []
    missing = []
    locked_ci_checked = []
    contracts = {row["repository"].casefold(): row["locked_ci"]
                 for row in registry["consumers"] if "locked_ci" in row}
    if contracts:
        vendor_consumers.validate(registry)
    names = repository_names
    if names is None:
        names = monitored_repositories(registry, token=token)
    for name in names:
        contract = contracts.get(name.casefold())
        entry = document["heads"].get(name)
        if entry is None:
            (failures if contract else missing).append({
                "repository": name,
                "message": "monitored repository has no child-heads entry",
            })
            continue
        if contract:
            try:
                locked_ci_checked.append(_verify_locked_ci(name, entry["sha"], contract))
            except (ValueError, OSError, subprocess.SubprocessError):
                failures.append({
                    "repository": name,
                    "message": "required locked CI workflow missing, changed, or unreadable: "
                               + contract["workflow"],
                })
                continue
        text = _fetch_repo_text(name, CHILD_CI_WORKFLOW_PATH, entry["sha"], _api_token(token))
        if text is None:
            if contract:
                continue  # Explicitly approved existing lane replaces the optional template.
            missing.append({
                "repository": name,
                "message": "missing copied " + CHILD_CI_WORKFLOW_PATH,
            })
            continue
        try:
            verify_workflow_template_copy(text, template_text)
        except ValueError as error:
            failures.append({"repository": name, "message": str(error)})
    return {"failures": failures, "missing": missing, "locked_ci_checked": locked_ci_checked}


def _dispatch_parent_workflow(parent_repository, workflow_path, ref, token):
    parent_repository = _normalize_repository(parent_repository)
    api_repo = "https://api.github.com/repos/" + parent_repository
    _github_api(
        "POST",
        api_repo + "/actions/workflows/" + quote(workflow_path, safe="/") + "/dispatches",
        token,
        {"ref": ref},
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
    pull_number = value.get("pull_number")
    if pull_number is not None:
        if isinstance(pull_number, str) and pull_number.isdigit():
            pull_number = int(pull_number)
        if type(pull_number) is not int or pull_number < 1:
            raise ValueError("invalid optional pull_number on child head")
        entry["pull_number"] = pull_number
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
    if type(pull_number) is not int or pull_number < 1:
        raise ValueError("expected positive pull_number")
    repository_id = _coerce_repository_id(document.get("repository_id"))
    result = {
        "schema": PARENT_PR_REF_SCHEMA,
        "repository": repository,
        "pull_number": pull_number,
        "repository_id": repository_id,
    }
    commit = document.get("commit")
    if commit is None:
        raise ValueError("expected commit SHA in parent-pr.ref.json")
    _hex(commit, 40)
    if commit == "0" * 40:
        raise ValueError("invalid placeholder commit in parent-pr.ref.json")
    result["commit"] = commit
    optional = document.get("optional")
    if optional is not None:
        result["optional"] = _normalize_optional_signals(optional)
    return result


def verify_repository_id_identity(*, expected_repository_id, resolved_repository_id):
    """Match repository id only (child-heads PR validation)."""
    expected_repository_id = _coerce_repository_id(expected_repository_id)
    resolved_repository_id = _coerce_repository_id(resolved_repository_id)
    if resolved_repository_id != expected_repository_id:
        raise ValueError(
            "repository id mismatch: expected {exp} got {got}".format(
                exp=expected_repository_id, got=resolved_repository_id,
            )
        )
    return True


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
        if type(expected_pull_number) is not int or expected_pull_number < 1:
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


def _live_consumer_repository_record(repository, *, token=None, repo_fetch=None):
    if repo_fetch is not None or token:
        return fetch_repository_record(repository, token, repo_fetch=repo_fetch)
    return public_repository_page_record(repository)


def fetch_repository_record(repository, token=None, repo_fetch=None):
    repository = _normalize_repository(repository)
    if repo_fetch is not None:
        record = repo_fetch(repository)
    elif token:
        record = _github_api("GET", "https://api.github.com/repos/" + repository, token)
    else:
        try:
            record = public_repository_page_record(repository)
        except PublicGitNotFound:
            raise PublicGitNotFound("not accessible") from None
        except PublicGitAccessBlocked:
            raise
    if not isinstance(record, dict) or record.get("id") is None:
        raise ValueError("invalid repository response for " + repository)
    return record


def fetch_child_head(repository, ref="main", *, remotes=None):
    repository = _normalize_repository(repository)
    if remotes is None:
        branch = "main" if ref in ("main", "master") else ref
        return public_ref_sha(repository, branch)
    remote = _repository_remote(repository, remotes)
    if ref in ("main", "master"):
        try:
            branch = _default_branch_name(remote)
        except ValueError:
            branch = ref
    else:
        branch = ref
    return _git_branch_tip(remote, branch)


def fetch_child_head_record(repository, ref="main", *, remotes=None, token=None, repo_fetch=None,
                            prior_record=None):
    sha = fetch_child_head(repository, ref, remotes=remotes)
    record = _live_consumer_repository_record(repository, token=token, repo_fetch=repo_fetch)
    if not record_is_public_monitored(record):
        raise ValueError("child is not a public non-archived repository")
    live_id = _coerce_repository_id(record["id"])
    entry = {"sha": sha, "repository_id": live_id}
    if prior_record is not None:
        verify_repository_id_identity(
            expected_repository_id=prior_record["repository_id"],
            resolved_repository_id=live_id,
        )
        optional = prior_record.get("optional")
        if isinstance(optional, dict):
            entry["optional"] = dict(optional)
    optional = {}
    if isinstance(record.get("created_at"), str):
        optional["repository_created_at"] = record["created_at"]
    if optional:
        entry["optional"] = {**entry.get("optional", {}), **optional}
    pull_number = _lookup_child_main_pull_number(repository, sha, token)
    if pull_number is not None:
        entry["pull_number"] = pull_number
    return entry


def refresh_child_heads(registry, heads_path, *, ref="main", remotes=None, token=None, repo_fetch=None):
    names = monitored_repositories(registry, token=token)
    prior_heads = {}
    heads_path_obj = Path(heads_path)
    if heads_path_obj.is_file():
        prior_heads = validate_child_heads(
            json.loads(heads_path_obj.read_text(encoding="utf-8")),
        )["heads"]
    heads = {
        name: fetch_child_head_record(
            name, ref, remotes=remotes, token=token, repo_fetch=repo_fetch,
            prior_record=prior_heads.get(name),
        )
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
                raise ValueError(BRANCH_DIFF_SCAN_ERROR)
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


def source_diff_report(registry, *, token=None, remotes=None, local_roots=None,
                       repository_names=None):
    names = repository_names
    if names is None:
        names = monitored_repositories(registry, token=token)
    findings = []
    scan_errors = []
    for name in names:
        try:
            findings.extend(branches_differing_from_main(
                name, remotes=remotes, local_roots=local_roots,
            ))
        except ValueError:
            scan_errors.append({"repository": name, "message": BRANCH_DIFF_SCAN_ERROR})
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
    branch_ref = api_repo + "/git/refs/heads/" + quote(branch, safe="")
    try:
        _github_api("GET", api_repo + "/git/ref/heads/" + quote(branch, safe=""), token)
        _github_api("PATCH", branch_ref, token, {"sha": base_sha, "force": True})
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
                        repo_fetch=None, skip_source_diff=False, pr_validation=False):
    document = validate_child_heads(document)
    ref = document["ref"]
    monitored = monitored_repositories(registry, token=token)
    drift = []
    identity_failures = []
    missing_heads = []
    extra_heads, extra_heads_excluded = _split_extra_heads(
        document["heads"], monitored, token=token, repo_fetch=repo_fetch,
    )
    live_heads = {}
    for name in monitored:
        recorded = document["heads"].get(name)
        if recorded is None:
            missing_heads.append(name)
            continue
        live = fetch_child_head_record(
            name, ref, remotes=remotes, token=token, repo_fetch=repo_fetch,
            prior_record=recorded,
        )
        live_heads[name] = live
        if recorded["sha"] != live["sha"]:
            drift.append({
                "repository": name,
                "recorded": recorded["sha"],
                "live": live["sha"],
            })
        try:
            verify_repository_id_identity(
                expected_repository_id=recorded["repository_id"],
                resolved_repository_id=live["repository_id"],
            )
        except ValueError as error:
            identity_failures.append({
                "repository": name,
                "message": str(error),
            })
    if skip_source_diff:
        diff_report = {"schema": "source-diff-report/1", "findings": [], "finding_count": 0,
                       "scan_errors": [], "scan_error_count": 0, "skipped": True}
    else:
        diff_report = source_diff_report(
            registry, token=token, remotes=remotes, local_roots=local_roots,
            repository_names=monitored,
        )
    parent_root_path = Path(parent_root)
    template_text = (parent_root_path / CHILD_CI_TEMPLATE).read_text(encoding="utf-8")
    audit_blocked, template_report, child_files = _baseline_audit_blocked(
        registry, document, monitored, template_text, token=token,
        parent_root=parent_root, parent_repository=parent_repository,
        remotes=remotes, local_roots=local_roots,
    )
    template_failures = template_report["failures"]
    template_missing = template_report["missing"]
    audit_skipped = []
    for row in template_failures:
        audit_skipped.append({
            "repository": row["repository"],
            "kind": "workflow_template",
            "message": row["message"],
        })
    for row in template_missing:
        audit_skipped.append({
            "repository": row["repository"],
            "kind": "workflow_template_missing",
            "message": row["message"],
        })
    for row in child_files["failures"]:
        audit_skipped.append({
            "repository": row["repository"],
            "kind": "adopted_child_files",
            "message": row["message"],
        })
    audit_failure_count = len(template_failures) + child_files["failure_count"]
    with tempfile.TemporaryDirectory(prefix="vendor-parent-ci-") as directory:
        work_dir = _copy_parent_vendor_baseline(parent_root, directory)
        vendor_check = _offline_parent_vendor_check(work_dir)
    autofix_result = None
    heads_dropped_unmonitored_count = len(set(document["heads"]) - set(monitored))
    needs_heads_refresh = bool(drift) or bool(missing_heads)
    if autofix and needs_heads_refresh and token:
        proposed_heads = {
            name: fetch_child_head_record(
                name, ref, remotes=remotes, token=token, repo_fetch=repo_fetch,
                prior_record=document["heads"].get(name),
            )
            for name in monitored
        }
        drift_candidates = []
        for name in monitored:
            recorded = document["heads"].get(name)
            proposed = proposed_heads.get(name)
            if recorded is None or proposed is None:
                continue
            if recorded["sha"] != proposed["sha"]:
                drift_candidates.append(name)
        autofix_audit_document = document
        if drift_candidates:
            autofix_heads = dict(document["heads"])
            for name in drift_candidates:
                autofix_heads[name] = proposed_heads[name]
            autofix_audit_document = validate_child_heads({
                "schema": CHILD_HEADS_SCHEMA, "ref": ref, "heads": autofix_heads,
            })
        autofix_audit_blocked, _, _ = _baseline_audit_blocked(
            registry,
            autofix_audit_document,
            drift_candidates,
            template_text,
            token=token,
            parent_root=parent_root,
            parent_repository=parent_repository,
            remotes=remotes,
            local_roots=local_roots,
        )
        refresh_plan = plan_heads_refresh(
            registry, document, proposed_heads, template_text, token=token,
            audit_blocked=autofix_audit_blocked,
        )
        encoded_heads = validate_child_heads({
            "schema": CHILD_HEADS_SCHEMA, "ref": ref, "heads": refresh_plan["heads"],
        })
        branch = AUTOFIX_BRANCH_PREFIX
        autofix_result = _open_or_update_autofix_pr(
            parent_repository,
            branch,
            "chore: refresh child default-branch heads",
            "Automated refresh of `child-heads.json` after live `main` drift.\n\n"
            "Parent CI never merges this pull request automatically.\n\n"
            "If the autofix pull request does not run checks automatically, ensure a GitHub App "
            "token or re-run `workflow_dispatch` on the autofix branch.",
            {"child-heads.json": _encode_child_heads(encoded_heads)},
            token,
        )
        workflow_path = ".github/workflows/vendor-parent-child-ci.yml"
        _dispatch_parent_workflow(parent_repository, workflow_path, branch, token)
        autofix_result["workflow_dispatch"] = {
            "workflow": workflow_path,
            "ref": branch,
        }
        autofix_result["heads_refresh_skipped"] = refresh_plan["skipped"]
        autofix_result["heads_dropped_unmonitored_count"] = heads_dropped_unmonitored_count
    drift_failures = len(drift) + len(missing_heads) + len(extra_heads)
    if pr_validation:
        failure_count = len(identity_failures) + len(extra_heads) + audit_failure_count
    else:
        failure_count = drift_failures + len(identity_failures) + len(extra_heads) + audit_failure_count
    if autofix and autofix_result and needs_heads_refresh:
        failure_count = len(identity_failures) + len(extra_heads) + audit_failure_count
    return {
        "schema": "parent-child-ci/1",
        "ref": ref,
        "monitored_count": len(monitored),
        "missing_heads": missing_heads,
        "extra_heads": extra_heads,
        "extra_heads_excluded_count": extra_heads_excluded,
        "drift": drift,
        "drift_count": len(drift),
        "identity_failures": identity_failures,
        "identity_failure_count": len(identity_failures),
        "template_failures": template_failures,
        "template_failure_count": len(template_failures),
        "template_missing": template_missing,
        "template_missing_count": len(template_missing),
        "locked_ci_checked": template_report.get("locked_ci_checked", []),
        "source_diff": diff_report,
        "parent_vendor_check": vendor_check,
        "adopted_child_files": child_files,
        "needs_heads_refresh": needs_heads_refresh,
        "heads_dropped_unmonitored_count": heads_dropped_unmonitored_count,
        "autofix": autofix_result,
        "heads_refresh_skipped": (autofix_result or {}).get("heads_refresh_skipped", []),
        "audit_skipped": audit_skipped,
        "audit_skipped_count": len(audit_skipped),
        "audit_failure_count": audit_failure_count,
        "audit_blocked": audit_blocked,
        "pr_validation": pr_validation,
        "failure_count": failure_count,
    }


def check_child_heads(registry, document, *, remotes=None, token=None, parent_repository=PARENT_REPOSITORY,
                        local_roots=None, local_consumers=None):
    document = validate_child_heads(document)
    ref = document["ref"]
    drift = []
    live_heads = {}
    for name in monitored_repositories(registry, token=token):
        recorded = document["heads"].get(name)
        if recorded is None:
            raise ValueError("child-heads missing repository: " + name)
        live = fetch_child_head_record(
            name, ref, remotes=remotes, token=token, prior_record=recorded,
        )
        live_heads[name] = live
        if recorded["sha"] != live["sha"]:
            drift.append({
                "repository": name,
                "recorded": recorded["sha"],
                "live": live["sha"],
            })
    monitored = monitored_repositories(registry, token=token)
    extra, extra_excluded = _split_extra_heads(
        document["heads"], monitored, token=token,
    )
    missing = [name for name in monitored if name not in document["heads"]]
    if extra:
        raise ValueError("child-heads has unknown public repositories: " + ", ".join(sorted(extra)))
    if missing:
        raise ValueError("child-heads missing monitored repositories: " + ", ".join(missing))
    pin_report = audit_registry_merged_pins(
        registry,
        parent_repository=parent_repository,
        token=token,
        remotes=remotes,
        local_roots=local_roots,
        local_consumers=local_consumers,
        consumer_revisions=_head_sha_map(document["heads"]),
    )
    return {
        "schema": "child-heads-check/1",
        "ref": ref,
        "drift": drift,
        "drift_count": len(drift),
        "pin_audit": pin_report,
        "failure_count": len(drift) + pin_report["failure_count"],
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
    if type(pull_number) is not int or pull_number < 1:
        raise ValueError("expected positive pull request number")
    fetch = pr_fetch or _fetch_parent_pull_request
    pull = fetch(parent_repository, pull_number, token=token)
    repo_record = fetch_repository_record(parent_repository, token, repo_fetch=repo_fetch)
    repository_id = _coerce_repository_id(repo_record["id"])
    if type(pull.get("number")) is not int or pull["number"] != pull_number:
        raise ValueError("pull number mismatch")
    base_repo = (pull.get("base") or {}).get("repo") or {}
    if (_coerce_repository_id(base_repo.get("id")) != repository_id or
            str(base_repo.get("full_name", "")).casefold() != parent_repository.casefold()):
        raise ValueError("pull base repository id/name mismatch")
    merged = bool(pull.get("merged_at"))
    if not merged:
        if pull.get("state") != "open":
            raise ValueError("parent pull request is closed without merge")
        head_repo = (pull.get("head") or {}).get("repo") or {}
        if (_coerce_repository_id(head_repo.get("id")) != repository_id or
                str(head_repo.get("full_name", "")).casefold() != parent_repository.casefold()):
            raise ValueError("unmerged parent pull request must belong to parent repository")
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


def _reject_merged_pull_request_intermediate_pin(repository, commit, token):
    """Reject a commit that belongs to a PR merged into the default branch under another merge SHA.

    Commit-to-PR lookup is gh_identity.commit_pulls; the policy stays here.
    """
    repository = _normalize_repository(repository)
    try:
        repo_record = _github_json("https://api.github.com/repos/" + repository, token)
    except HTTPError:
        return
    default_branch = repo_record.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        return
    ghi = _gh_identity()
    try:
        result = _ghi_call(ghi.commit_pulls, repository, commit.lower(),
                           fetch=lambda url: _github_json(url, token))
    except (HTTPError, ghi.Error):
        return
    for pull in result["pulls"]:
        if not pull.get("merged_at"):
            continue
        if pull.get("base_ref") != default_branch:
            continue
        merge_sha = pull.get("merge_commit_sha")
        if isinstance(merge_sha, str) and merge_sha.lower() != commit.lower():
            raise ValueError(PIN_NOT_MERGED_ERROR)


def _parent_git_runner(args, cwd):
    """git runner for gh_identity.first_parent_check: this module's sanitized env, no shell.

    gh_identity always prefixes args with credential.helper=/core.askPass=/http.extraHeader=;
    no token is ever placed in git arguments or remotes.
    """
    result = subprocess.run(["git", "-C", cwd] + list(args), env=_git_subprocess_env(),
                            capture_output=True, timeout=120)
    return result.returncode, result.stdout


def _commit_merged_via_git_bare_fetch(repository, commit, *, remotes=None):
    """Exact first-parent check via gh_identity.first_parent_check (blob-less bare fetch)."""
    ghi = _gh_identity()
    remote = _repository_remote(repository, remotes)
    try:
        result = ghi.first_parent_check(_normalize_repository(repository), commit.lower(), exact=True,
                                        remote=remote, git_runner=_parent_git_runner, timeout=300)
    except (ghi.Error, subprocess.SubprocessError, OSError):
        raise ValueError(PUBLIC_GIT_FETCH_ERROR) from None
    if result["reason"] == "commit_not_found":
        raise ValueError(PUBLIC_GIT_FETCH_ERROR)
    if result["contains"] is not True:
        raise ValueError(PIN_NOT_MERGED_ERROR)
    return True


def commit_merged_into_default(repository, commit, *, remotes=None, local_roots=None, token=None):
    """Return True when commit is on the default branch first-parent history.

    Rejects no-ff merge PR intermediate commits; accepts squash and merge commits.
    Rebase-merged PR intermediates may also pass (first-parent linearization).
    """
    repository = _normalize_repository(repository)
    _hex(commit, 40)
    local_roots = local_roots or {}
    remotes = remotes or {}
    if repository in local_roots:
        root = Path(local_roots[repository])
        for branch in ("main", "master"):
            result = subprocess.run(
                ["git", "-C", str(root), "rev-list", "--first-parent", branch],
                env=_git_subprocess_env(),
                capture_output=True,
                timeout=120,
            )
            if result.returncode:
                continue
            if any(line.lower() == commit.lower()
                   for line in result.stdout.decode().splitlines()):
                return True
        raise ValueError(PIN_NOT_MERGED_ERROR)
    _commit_merged_via_git_bare_fetch(repository, commit, remotes=remotes)
    api_token = _api_token(token)
    if api_token:
        _reject_merged_pull_request_intermediate_pin(repository, commit, api_token)
    return True


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
        r"uses:\s*" + re.escape(parent_repository) + r"/(?P<workflow>[^\s@]+)@(?P<ref>[0-9a-f]{40})\b"
    )
    checkout_repo = re.compile(r"^\s+repository:\s*" + re.escape(parent_repository) + r"\s*$")
    ref_sha = re.compile(r"^\s+ref:\s*([0-9a-f]{40})\s*$")
    for path, text in sorted(workflows.items()):
        if not isinstance(text, str):
            raise ValueError("workflow content must be text: " + path)
        lines = text.splitlines()
        for index, line in enumerate(lines, 1):
            match = uses_parent.search(line)
            if match:
                ref = match.group("ref")
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
                               remotes=None, local_roots=None, token=None):
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
        key = (pin["repository"], pin["commit"])
        if key in checked:
            continue
        checked.add(key)
        try:
            commit_merged_into_default(
                pin["repository"],
                pin["commit"],
                remotes=remotes,
                local_roots=local_roots,
                token=token,
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
    branch = "main" if ref in ("main", "master") else ref
    return public_ref_sha(repository, branch)


def _fetch_repo_text(repository, path, ref, token=None):
    raw = public_raw_bytes(repository, ref, path)
    if raw is None:
        return None
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
            token=token,
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
            if report.get("pr_validation"):
                lines.append(
                    f"**{report['drift_count']} child head(s) drifted from child-heads.json "
                    "(report only on pull request validation).**"
                )
            else:
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
        if report.get("template_missing_count"):
            lines.append(
                f"**{report['template_missing_count']} monitored repository/repositories without copied child CI workflow (not enforced).**"
            )
            for row in report.get("template_missing", []):
                lines.append(f"- `{row.get('repository', '')}`: {row.get('message', '')}")
            lines.append("")
        for row in report.get("locked_ci_checked", []):
            lines.append(f"Approved locked CI workflow: `{row['repository']}` / "
                         f"`{row['workflow']}` (parent-pinned SHA-256 verified).")
        if report.get("locked_ci_checked"):
            lines.append("")
        child_files = report.get("adopted_child_files", {})
        lines.append(f"Adopted child/source file audit: {len(child_files.get('checked', []))} consumers checked; "
                     f"{child_files.get('failure_count', 0)} failures.")
        for row in child_files.get("failures", []):
            lines.append(f"- `{row['repository']}`: {row['message']}")
        lines.append("")
        if report.get("audit_failure_count"):
            lines.append(
                f"**{report['audit_failure_count']} adopted baseline audit failure(s) "
                "(fail CI; autofix keeps the recorded head for affected repositories).**"
            )
            for row in report.get("audit_skipped", []):
                if row.get("kind") in ("workflow_template", "adopted_child_files"):
                    lines.append(
                        f"- `{row.get('repository', '')}` ({row.get('kind', '')}): {row.get('message', '')}"
                    )
            lines.append("")
        if report.get("extra_heads_excluded_count"):
            lines.append(
                f"**{report['extra_heads_excluded_count']} non-public or archived "
                "child-heads entr(ies) omitted from output.**"
            )
            lines.append("")
        dropped = report.get("heads_dropped_unmonitored_count", 0)
        if dropped:
            lines.append(
                f"**{dropped} stale child-heads entr(ies) removed from autofix output "
                "(no longer monitored; names omitted).**"
            )
            lines.append("")
        if report.get("missing_heads"):
            lines.append("**Missing monitored repositories in child-heads.json:**")
            for name in report["missing_heads"]:
                lines.append(f"- `{name}`")
            lines.append("")
        if report["source_diff"]["finding_count"]:
            lines.append(f"**{report['source_diff']['finding_count']} branch tip(s) differ from default branch.**")
            lines.append("")
            for row in report["source_diff"]["findings"]:
                lines.append(f"- `{row['repository']}` branch `{row['branch']}` @ `{row['tip']}`")
            lines.append("")
        if report["source_diff"].get("scan_error_count"):
            lines.append(
                f"**{report['source_diff']['scan_error_count']} source-diff scan error(s) (report only).**"
            )
            lines.append("")
        if not report["failure_count"]:
            lines.append(
                "Child heads match, locked child workflow templates match, and parent vendor baseline check passed."
            )
            if report["source_diff"]["finding_count"]:
                lines.append(
                    "Source-diff branch findings were reported above but do not fail this workflow."
                )
        Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines = ["## Child head and pin audit", ""]
    if report["drift_count"]:
        lines.append(f"**{report['drift_count']} child head(s) drifted from child-heads.json.**")
        lines.append("")
    failures = report.get("pin_audit", {}).get("failures", [])
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
            "run-child-ci",
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
    parser.add_argument("--pr-validation", action="store_true",
                        help="pull request mode: SHA drift is report-only; only repository id mismatch fails")
    parser.add_argument("--consumer-name", dest="consumer_name",
                        help="consumer repository for run-child-ci (default: derive from --root)")
    args = parser.parse_args(argv)
    try:
        if args.mode == "run-child-ci":
            root = Path(args.root).resolve()
            consumer = args.consumer_name
            if not consumer:
                remote = subprocess.run(
                    ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if remote.returncode != 0 or not remote.stdout.strip():
                    raise ValueError("--consumer-name required when origin remote is unavailable")
                raw_remote = remote.stdout.strip()
                if raw_remote.startswith("git@"):
                    consumer = raw_remote.split(":", 1)[-1]
                else:
                    consumer = raw_remote.removeprefix("https://github.com/")
                consumer = _normalize_repository(consumer.removesuffix(".git"))
            token = _api_token(None)
            result = run_child_ci(
                root,
                args.ref_file,
                consumer,
                token=token,
                parent_repository=args.parent,
                get=get,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.mode == "run-parent-child-ci":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            document = json.loads(Path(args.heads).read_text(encoding="utf-8"))
            token = None
            if args.autofix:
                token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            result = run_parent_child_ci(
                registry,
                document,
                parent_root=args.root,
                token=token,
                autofix=args.autofix,
                parent_repository=args.parent,
                skip_source_diff=args.skip_source_diff,
                pr_validation=args.pr_validation,
            )
            if args.summary:
                _write_child_heads_summary(result, args.summary)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["failure_count"] else 0
        if args.mode == "refresh-child-heads":
            registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            token = _api_token(None)
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
