"""Vendor fleet: one read-only entry point for the cross-repository work (stdlib only).

The way ``python -m unittest discover`` finds tests, every command here finds its
repositories, locks, pins and tests from the records that already exist, builds one
dependency graph and aggregates the results::

    python -S vendor_fleet_plan.py                                  # catalog registration plan (unchanged)
    python -S vendor_fleet_plan.py targets --checkouts ..           # 🎯 target / 🚫 excluded / ❔ insufficient
    python -S vendor_fleet_plan.py graph --checkouts .. --check     # 🔗 edges, 🔁 mutual repos, ⭕ execution cycles
    python -S vendor_fleet_plan.py targets --checkouts .. --at heads # the revisions child-heads.json records
    python -S vendor_fleet_plan.py impact py_launch.cjs --checkouts ..
    python -S vendor_fleet_plan.py plan --checkouts ..              # 🌊 dry-run: vendor → regenerate → verify → record
    python -S vendor_fleet_plan.py plan --changed py_launch.cjs --checkouts .. --apply
    python -S vendor_fleet_plan.py test --checkouts .. -p 'test_runtime*.py' -k compat --jobs 4
    python -S vendor_fleet_plan.py report --fetch --previous old.json --format markdown
    python -S vendor_fleet_plan.py query deps Ironmate            # from the published fleet.jsonld, no checkouts
    python -S vendor_fleet_plan.py query who web-ui               # name, previous name or repository_id → identity
    python -S vendor_fleet_plan.py query facts xprobe --severity updated

Inputs, none of them new: the parent's ``vendor-consumers.json``, ``child-heads.json``,
``vendor-catalog.json`` and ``vendor.lock.json``; every repository's lock files
(``vendor.lock.json``, ``shared-tools.lock.json``, ``html-import-vendor/1``), a hub's own
``child-heads.json`` (Aoi), workflow pins to the parent and the test layout.
``--checkouts DIR`` holds clones named like the repositories; the parent is read from
its working tree. ``--fetch`` clones a missing public repository once (anonymous,
bare, blob-less, depth 1) and fetches the blobs a run needs in one batch per
repository; every tree and blob is read once per run. Without ``--fetch`` nothing
touches the network.

Writes: none, except ``plan --apply``, which runs the existing
``vendor_sync.py update --only DEST --to-ref SHA`` in local checkouts. Tests run in
disposable detached worktrees with a cleaned environment (no PYTHONPATH, VIRTUAL_ENV,
PYTEST_ADDOPTS or GIT_DIR-style redirections from the caller), one subprocess per test directory.

Exit codes: 0 = ok; 1 = with ``--check``, a remaining finding, and for ``test`` /
``plan --apply`` a failing or blocked step; 2 = input error. The registration plan
(no command) keeps its own codes.
"""
from __future__ import annotations

import argparse, ast, datetime, fnmatch, graphlib, hashlib, importlib, json, os, re, shutil, subprocess, sys, tempfile, time, unittest
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
OWNER = "myon-bioinformatics"
PARENT = "myon-bioinformatics"  # short name of myon-bioinformatics/myon-bioinformatics
REPORT_SCHEMA = "vendor-fleet-report/1"
WORKTREE = "WORKTREE"
STATUSES = ("success", "failure", "updated", "skipped", "blocked", "unavailable")
ICON = {"success": "✅", "failure": "❌", "updated": "🔄", "skipped": "⏭️", "blocked": "⛔", "unavailable": "❔",
        "target": "🎯", "excluded": "🚫", "insufficient": "❔"}
LAUNCHERS = ("py_launch.cjs", "node_launch.py")
SKIP_PARTS = {".git", "node_modules", "__pycache__", ".venv", "fixtures", "dist", "build", ".fleet-cache"}
# Repository redirections and interpreter/test overrides that must not reach another repository's checks.
# Transport settings (GIT_CONFIG_* proxies, GIT_SSL_CAINFO) stay: they describe the machine, not a checkout.
DROPPED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
               "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
               "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONSAFEPATH", "VIRTUAL_ENV",
               "CONDA_PREFIX", "UV_PROJECT_ENVIRONMENT", "UV_PROJECT", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")
RESULT_MARK = "FLEET-RESULT "
LOCK_NAMES = ("vendor.lock.json", "shared-tools.lock.json")
# One public, fixed codebook for the fleet graph (TalkCompress rule: shared, reproducible codes only, no ad-hoc
# abbreviations). Classes are inferred from the node string; each relation reads "target <relation> source".
VOCAB = "https://myon-bioinformatics.github.io/myon-bioinformatics/fleet#"
CLASSES = {
    "Repository": ("📦", "a repository (\"Repo:\"), identified by its GitHub repository_id when a hub records it"),
    "Directory": ("📁", "a subtree prefix of a hub (\"Hub:prefix/\")"),
    "Record": ("🗂️", "a record the fleet writes back: a lock, child-heads.json or vendor-catalog.json"),
    "Workflow": ("⚙️", "a GitHub Actions workflow file"),
    "File": ("📄", "any other tracked file"),
    "Entry": ("🔖", "one entry of a record (\"Repo:path#key\"): a proposition, a ledger judgement, a corpus case"),
    "Run": ("🏃", "a GitHub Actions run, or one attempt of it (\"Repo:actions/runs/N[/attempts/M]\")"),
    "PullRequest": ("🔀", "a pull request (\"Repo:#N\"), repository-qualified so equal numbers never meet"),
}
RELATIONS = {  # kind: code, icon, domain (source), range (target), group, plan phase, PROV parent, label
    "vendor": ("vd", "📥", "File", "File", "code", "vendor", "prov:wasDerivedFrom", "copies the bytes of"),
    "launcher": ("ln", "🚀", "File", "File", "code", "vendor", "prov:wasDerivedFrom", "copies the shared launcher"),
    "license": ("lc", "⚖️", "File", "File", "code", "vendor", "prov:wasDerivedFrom", "copies the license of"),
    "workflow-pin": ("wp", "📌", "File|Workflow|Repository", "Workflow", "code", "regenerate", "prov:used", "runs a pinned commit of"),
    "subtree": ("st", "🌿", "Directory", "Repository", "code", "regenerate", "prov:wasDerivedFrom", "is pushed from the subtree"),
    "shared-tools": ("sh", "🧰", "File", "Repository", "code", None, None, "is a declared consumer of"),
    "heads": ("hd", "🧷", "Repository", "Record", "record", "record", "prov:used", "records the head of"),
    "catalog": ("ct", "📚", "File", "Record", "record", "record", "prov:used", "recommends a commit of"),
    "implied": ("in", "↳", "File|Workflow|Record|Directory", "Directory|Repository", "structure", None, None, "is part of"),
    # Evidence relations (fleet_records): source is what is depended on, target what to recheck when it changes.
    "tested_by": ("tb", "🧪", "File|Workflow|Record|Entry", "File|Workflow", "test", None, None,
                  "(source) is exercised by the test or workflow (target)"),
    "produced_by": ("pb", "🏭", "File", "File|Record", "provenance", None, "prov:wasGeneratedBy", "(target) is generated by (source)"),
    "derived_from": ("df", "🧬", "File|Record|Entry|Directory|Repository", "Entry|Record|File", "provenance", None,
                     "prov:wasDerivedFrom", "(target) was computed or recorded from (source)"),
    "observed_in": ("oi", "👁️", "Run", "File|Entry", "evidence", None, "prov:wasGeneratedBy", "(target) was captured from the run (source)"),
    "verified_by": ("vb", "🔍", "File|Entry", "File|Record|Entry", "evidence", None, None, "(source) is checked by the record (target)"),
    "supports": ("sp", "👍", "Entry", "Entry", "evidence", None, None, "(source) supports (target)"),
    "contradicts": ("cd", "👎", "Entry", "Entry", "evidence", None, None, "(source) contradicts (target)"),
    "depends_on": ("pd", "🔀", "PullRequest", "PullRequest", "pr", None, None, "(target) waits for (source) to merge"),
}
EVIDENCE_KINDS = ("tested_by", "produced_by", "derived_from", "observed_in", "verified_by", "supports", "contradicts", "depends_on")
UPDATE_KINDS = tuple(k for k, r in RELATIONS.items() if r[5] == "vendor")
CODE_KINDS = {k for k, r in RELATIONS.items() if r[4] == "code"}
ICON_OF = {k: r[1] for k, r in RELATIONS.items()}
RECORD_NAMES = {"child-heads.json", "vendor-catalog.json", *LOCK_NAMES}
PARENT_REFRESH = ["python", "-S", "vendor_sync.py", "refresh-child-heads", "--registry", "vendor-consumers.json",
                  "--heads", "child-heads.json"]
__all__ = ["plan", "Fleet", "load_records", "discover", "build_graph", "parse_node", "impact", "plan_updates",
           "discover_tests", "test_jobs", "execute", "apply_plan", "report", "remaining", "diff_reports", "render",
           "node_class", "check_shapes", "compress_rows", "expand_facts", "kept_identifiers", "to_jsonld", "load_graph",
           "query", "query_deps", "query_who", "query_facts", "query_why", "query_reverify", "render_query", "unittest_worker",
           "impact_view", "why", "collect_evidence", "EVIDENCE_KINDS", "main"]


def _sibling(name):
    """Import a parent module on demand; the unittest worker runs with -I and must not see them."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(name)


def plan(catalog, registry, *, active_consumers=None):
    """Identify catalog registrations to review, without guessing file destinations.

    active_consumers optionally limits work to explicitly selected repositories.
    Legacy consumers are reported separately; neither missing locks nor absent
    source entries imply a successful enrollment or compatible upgrade.
    """
    catalog = _sibling("vendor_catalog").validate(catalog)
    registry = _sibling("vendor_consumers").validate(registry)
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
            # The canonical owner must not vendor its own source (registry contract).
            if tool["repository"].casefold() == name.casefold():
                continue
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


# ---------------------------------------------------------------- repository access (read once per run)

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def short(name):
    """'owner/Repo', a GitHub URL or 'Repo' -> 'Repo' for this owner; other owners stay 'owner/Repo'."""
    name = str(name).strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    return name.split("/", 1)[1] if name.startswith(OWNER + "/") else name


def split(node):
    repo, _, path = node.partition(":")
    return repo, path


def git_blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def clean_env(**extra):
    """Environment without Git repository redirections or Python/uv/pytest path overrides from the caller."""
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
    env.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", **extra)
    return env


class Fleet:
    """Read-only access to repositories through local Git data, cached for one run.

    ``checkouts`` is a directory of clones named like the repositories (worktree or
    bare ``NAME.git``). The parent is ``parent_root``; at ``main`` it is read from the
    working tree so local record edits are what gets checked. ``fetch=True`` clones a
    missing public repository once into ``checkouts/.fleet-cache`` (anonymous).
    """

    def __init__(self, parent_root=ROOT, checkouts=None, *, fetch=False, at="main", repos=None):
        self.parent_root = Path(parent_root).resolve()
        self.checkouts = Path(checkouts).resolve() if checkouts else None
        self.fetch = fetch
        self.at = at
        self.only = ({short(x) for x in repos} | {PARENT}) if repos else None
        self.stats = {"git_calls": 0, "clones": 0, "fetches": 0, "cache_hits": 0}
        self.unavailable = {}
        self.recorded = {}  # at="heads": the parent's child-heads.json revisions (set by load_records)
        self._cache = {}
        self._fetched = set()

    def _memo(self, key, compute):
        if key in self._cache:
            self.stats["cache_hits"] += 1
            return self._cache[key]
        value = self._cache[key] = compute()
        return value

    def run(self, directory, *args, timeout=600):
        self.stats["git_calls"] += 1
        return subprocess.run(["git", "-C", str(directory), "-c", "core.hooksPath=", *args], env=clean_env(),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)

    def selected(self, repo):
        return self.only is None or repo in self.only

    def _cached_clone(self, repo):
        return self.checkouts is not None and self.location(repo) == self.checkouts / ".fleet-cache" / (repo + ".git")

    def location(self, repo):
        """The Git directory holding repo, or None (reason in self.unavailable)."""
        def find():
            if repo == PARENT:
                return self.parent_root
            if "/" in repo:
                self.unavailable[repo] = "repository of another owner"
                return None
            if not self.checkouts:
                self.unavailable[repo] = "no checkout (use --checkouts DIR or --fetch)"
                return None
            for candidate in (self.checkouts / repo, self.checkouts / (repo + ".git"),
                              self.checkouts / ".fleet-cache" / (repo + ".git")):
                if (candidate / ".git").exists() or (candidate / "objects").is_dir():
                    return candidate
            if not self.fetch:
                self.unavailable[repo] = "no checkout (use --checkouts DIR or --fetch)"
                return None
            target = self.checkouts / ".fleet-cache" / (repo + ".git")
            target.parent.mkdir(parents=True, exist_ok=True)
            self.stats["clones"] += 1
            done = subprocess.run(["git", "-c", "core.hooksPath=", "clone", "--quiet", "--bare", "--filter=blob:none",
                                   "--depth", "1", "--no-tags", f"https://github.com/{OWNER}/{repo}.git", str(target)],
                                  env=clean_env(), stdin=subprocess.DEVNULL, capture_output=True, timeout=600, check=False)
            if done.returncode:
                shutil.rmtree(target, ignore_errors=True)
                self.unavailable[repo] = "public clone failed (missing, private or unreachable)"
                return None
            return target
        return self._memo(("location", repo), find)

    def _fetch_commit(self, repo, sha):
        """Add one commit to a local repository (only in --fetch mode); True when it is present afterwards."""
        location = self.location(repo)
        if location is None:
            return False
        if self.run(location, "cat-file", "-e", sha + "^{commit}").returncode == 0:
            return True
        if not self.fetch:
            return False
        self.stats["fetches"] += 1
        # Only clones made here are partial; a caller's full clone is never turned into a partial one.
        extra = ["--filter=blob:none"] if self._cached_clone(repo) else []
        self.run(location, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--depth", "1", *extra, "origin", sha)
        return self.run(location, "cat-file", "-e", sha + "^{commit}").returncode == 0

    def revision(self, repo, at=None):
        """Commit SHA for 'main' (origin/HEAD, else HEAD), 'heads' (recorded child head) or an explicit SHA.

        The parent itself is its working tree at 'main' and 'heads'.
        """
        at = at or self.at
        if at == "heads":
            at = "main" if repo == PARENT or repo not in self.recorded else self.recorded[repo]

        def resolve():
            location = self.location(repo)
            if location is None:
                return None
            if at == "main":
                if repo == PARENT:
                    return WORKTREE
                for ref in ("refs/remotes/origin/HEAD", "HEAD"):
                    done = self.run(location, "rev-parse", "--verify", "-q", ref + "^{commit}")
                    if done.returncode == 0:
                        return done.stdout.decode().strip()
                self.unavailable[repo] = "no default branch revision"
                return None
            if not re.fullmatch(r"[0-9a-f]{40}", at or ""):
                raise ValueError("revision must be 'main', 'heads' or a full commit SHA")
            if self._fetch_commit(repo, at):
                return at
            self.unavailable[repo] = "requested revision not available locally"
            return None
        return self._memo(("revision", repo, at), resolve)

    def files(self, repo, rev):
        """{path: blob} at rev; blobs are None for the working tree (computed on read)."""
        def listing():
            location = self.location(repo)
            if location is None or rev is None:
                return {}
            if rev == WORKTREE:
                done = self.run(location, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
                return {p: None for p in done.stdout.decode("utf-8", "replace").split("\0")
                        if p and (location / p).is_file() and not (location / p).is_symlink()}
            done = self.run(location, "ls-tree", "-r", "-z", "--full-tree", rev)
            if done.returncode:
                raise ValueError(f"cannot list {repo} at {rev[:12]}")
            out = {}
            for line in done.stdout.decode("utf-8", "replace").split("\0"):
                if line:
                    meta, _, path = line.partition("\t")
                    mode, kind, blob = meta.split()
                    if kind == "blob" and mode != "120000":
                        out[path] = blob
            return out
        return self._memo(("files", repo, rev), listing)

    def prefetch(self, repo, rev, paths):
        """One batch fetch of the blobs a blob-less clone made here still lacks (no-op otherwise)."""
        if rev in (None, WORKTREE) or not self.fetch or not self._cached_clone(repo):
            return
        files = self.files(repo, rev)
        wanted = sorted({files[p] for p in paths if p in files} - self._fetched)
        for start in range(0, len(wanted), 200):
            self.stats["fetches"] += 1
            self.run(self.location(repo), "-c", "fetch.negotiationAlgorithm=noop", "fetch", "--quiet", "--no-tags",
                     "--no-write-fetch-head", "--filter=blob:none", "origin", *wanted[start:start + 200])
        self._fetched.update(wanted)

    def read(self, repo, rev, path):
        files = self.files(repo, rev)
        if path not in files:
            return None
        if rev == WORKTREE:
            return self._memo(("worktree", repo, path), lambda: (self.location(repo) / path).read_bytes())
        blob = files[path]

        def cat():
            done = self.run(self.location(repo), "cat-file", "blob", blob)
            if done.returncode:
                raise ValueError(f"cannot read {repo}:{path}")
            return done.stdout
        return self._memo(("blob", repo, blob), cat)

    def blob(self, repo, rev, path):
        files = self.files(repo, rev)
        if path not in files:
            return None
        if files[path] is None:
            files[path] = git_blob(self.read(repo, rev, path))
        return files[path]

    def blob_at(self, repo, commit, path):
        """Blob id of path at another commit (pins, catalog commits); fetches that commit only with --fetch."""
        def resolve():
            if not commit or not self._fetch_commit(repo, commit):
                return None
            done = self.run(self.location(repo), "rev-parse", "--verify", "-q", f"{commit}:{path}")
            return done.stdout.decode().strip() if done.returncode == 0 else None
        return self._memo(("blob_at", repo, commit, path), resolve)

    def read_blob(self, repo, blob):
        """Bytes of one blob id (a file at another commit, from blob_at), or None."""
        if not blob:
            return None

        def cat():
            done = self.run(self.location(repo), "cat-file", "blob", blob)
            return None if done.returncode else done.stdout
        return self._memo(("blob", repo, blob), cat)

    def blobs_local(self, repo):
        """True unless the repository is a blob-less clone made here (wide reads would fetch every blob)."""
        return not self._cached_clone(repo)

    def json(self, repo, rev, path):
        data = self.read(repo, rev, path)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{repo}:{path}: invalid JSON: {exc}") from exc

    def display(self, value):
        """Local paths shown as <tools>/<parent>/<checkouts> so published reports carry no machine paths."""
        text = str(value)
        if self.checkouts:
            text = text.replace(str(self.checkouts), "<checkouts>")
        return text.replace(str(ROOT), "<tools>").replace(str(self.parent_root), "<parent>")

    def checkout_path(self, repo):
        """A worktree clone (not bare) that commands can run in, or None."""
        location = self.location(repo)
        return None if location is None or not (location / ".git").exists() else location


# ---------------------------------------------------------------- 1. discovery (targets / excluded / insufficient)

def load_records(fleet):
    """The parent's records at the analysed revision, validated by their own modules."""
    rev = fleet.revision(PARENT)
    documents = {}
    for name in ("vendor-consumers.json", "child-heads.json", "vendor-catalog.json"):
        documents[name] = fleet.json(PARENT, rev, name)
        if documents[name] is None:
            raise ValueError("parent record missing: " + name)
    records = {
        "registry": _sibling("vendor_consumers").validate(documents["vendor-consumers.json"]),
        "heads": _sibling("vendor_sync").validate_child_heads(documents["child-heads.json"]),
        "catalog": _sibling("vendor_catalog").validate(documents["vendor-catalog.json"]),
        "revision": rev,
    }
    fleet.recorded = {short(name): head["sha"] for name, head in records["heads"]["heads"].items()}
    return records


def _lock_paths(paths):
    return sorted(p for p in paths if PurePosixPath(p).name in LOCK_NAMES
                  and not SKIP_PARTS & set(p.split("/")[:-1]) and not p.startswith(("tests/", "test/")))


def prefixes_of(discovery):
    """{hub: {subtree prefix, ...}} from the hubs' child-heads.json records."""
    prefixes = {}
    for r in discovery["repositories"]:
        for hub, info in r["hubs"].items():
            if info.get("prefix"):
                prefixes.setdefault(hub, set()).add(info["prefix"])
    return prefixes


def discover(fleet, records=None, *, listing=None):
    """Classify every repository the records name (plus local clones and an optional owner listing).

    listing: optional public, non-archived inventory [{"name": ...}] (for example from the
    vendored gh_identity); a recorded repository missing from it is excluded. Private names
    are never part of the output.
    """
    records = records or load_records(fleet)
    rows = {}

    def row(name):
        return rows.setdefault(name, {"repository": name, "roles": set(), "hubs": {}, "locks": [], "reasons": [],
                                      "category": "", "revision": None, "legacy": None})
    row(PARENT)["roles"].add("hub")
    for consumer in records["registry"]["consumers"]:
        r = row(short(consumer["repository"]))
        r["roles"].add("consumer")
        if consumer["state"] == "legacy":
            r["legacy"] = consumer.get("reason") or "legacy"
        elif consumer.get("lock"):
            r["locks"].append(consumer["lock"])
        for entry in consumer["entries"]:
            row(short(entry["repository"]))["roles"].add("source")
    def previous(head):
        return sorted(short(n) for n in (head.get("optional") or {}).get("previous_names") or [])
    for full, head in records["heads"]["heads"].items():
        r = row(short(full))
        r["roles"].add("child")
        r["hubs"][PARENT] = {"sha": head["sha"], "prefix": None, "repository_id": head.get("repository_id"),
                             "previous_names": previous(head)}
    for tool in records["catalog"]["tools"]:
        row(short(tool["repository"]))["roles"].add("catalog")
    if fleet.checkouts and fleet.checkouts.is_dir():
        for path in sorted(fleet.checkouts.iterdir()):
            name = path.name.removesuffix(".git")
            if not name.startswith(".") and ((path / ".git").exists() or (path / "objects").is_dir()):
                row(name)["roles"].add("observed")
    inventory = None if listing is None else {short(x["name"]) for x in listing if not x.get("private")}
    for name in sorted(inventory or ()):
        row(name)["roles"].add("listed")

    def scannable(name):
        r = rows[name]
        return (fleet.selected(name) and not r["legacy"] and r["roles"] - {"catalog", "source"}
                and (inventory is None or name in inventory or name == PARENT))
    # Hubs other than the parent declare their children in their own child-heads.json (Aoi).
    seen = {PARENT}
    while True:
        pending = [n for n in sorted(rows) if n not in seen and scannable(n)]
        if not pending:
            break
        for name in pending:
            seen.add(name)
            rev = rows[name]["revision"] = fleet.revision(name)
            if rev and "child-heads.json" in fleet.files(name, rev):
                rows[name]["roles"].add("hub")
                fleet.prefetch(name, rev, ["child-heads.json"])
                for full, head in (fleet.json(name, rev, "child-heads.json") or {}).get("heads", {}).items():
                    child = row(short(full))
                    child["roles"].add("child")
                    child["hubs"][name] = {"sha": head.get("sha"), "repository_id": head.get("repository_id"),
                                           "prefix": (head.get("optional") or {}).get("subtree_prefix"),
                                           "previous_names": previous(head)}
    rows[PARENT]["revision"] = fleet.revision(PARENT)
    registered = {short(c["repository"]) for c in records["registry"]["consumers"]}
    vendor_consumers = _sibling("vendor_consumers")
    for name, r in sorted(rows.items()):
        reasons = r["reasons"]
        if not fleet.selected(name):
            r["category"] = "excluded"
            reasons.append("outside --repos")
            continue
        if r["legacy"]:
            r["category"] = "excluded"
            reasons.append("legacy consumer: " + r["legacy"])
            continue
        if inventory is not None and name not in inventory and name != PARENT:
            r["category"] = "excluded"
            reasons.append("not in the public, non-archived inventory")
            continue
        if not r["roles"] - {"catalog", "source"}:
            r["category"] = "excluded"
            reasons.append("source only (not a consumer, hub, child or local clone)")
            continue
        rev = r["revision"]
        if rev is None:
            r["category"] = "insufficient"
            reasons.append(fleet.unavailable.get(name, "no revision"))
            continue
        paths = fleet.files(name, rev)
        r["lock_files"] = _lock_paths(paths)
        fleet.prefetch(name, rev, r["lock_files"])
        if "consumer" in r["roles"] and name != PARENT and PARENT not in r["hubs"]:
            reasons.append("registered consumer without a child-heads.json entry")
        if PARENT in r["hubs"] and name not in registered:
            reasons.append("child-heads.json entry without registry topology")
        for lock in r["locks"]:
            if lock not in paths:
                reasons.append("registered lock missing: " + lock)
                continue
            try:
                compared = vendor_consumers.compare(records["registry"], fleet.json(name, rev, lock), OWNER + "/" + name)
            except (ValueError, KeyError, TypeError) as exc:
                reasons.append(f"registered lock unreadable: {lock} ({exc})")
                continue
            if not compared["matches"]:
                reasons.append(f"registry ≠ {lock}: missing {len(compared['missing'])}, unexpected {len(compared['unexpected'])}")
        if r["lock_files"] and not (r["hubs"] or name == PARENT or name in registered):
            reasons.append("lock outside every hub: " + ", ".join(r["lock_files"]))
        r["category"] = "insufficient" if reasons else "target"
    out = []
    for name, r in sorted(rows.items(), key=lambda x: x[0].casefold()):
        ids = sorted({str(i["repository_id"]) for i in r["hubs"].values() if i.get("repository_id")})
        if len(ids) > 1:  # two hubs disagree about who this name is
            r["reasons"].append("identity conflict: repository_id " + " ≠ ".join(ids))
            if r["category"] == "target":
                r["category"] = "insufficient"
        out.append({"repository": name, "category": r["category"], "roles": sorted(r["roles"]),
                    "repository_id": int(ids[0]) if len(ids) == 1 else None, "hubs": dict(sorted(r["hubs"].items())),
                    "previous_names": sorted({n for i in r["hubs"].values() for n in i.get("previous_names") or []}),
                    "revision": r["revision"], "lock_files": r.get("lock_files", []), "reasons": r["reasons"]})
    counts = {c: sum(x["category"] == c for x in out) for c in ("target", "excluded", "insufficient")}
    return {"repositories": out, "counts": counts}


# ---------------------------------------------------------------- 2. dependency graph

def _kind(source_path, destination):
    base = PurePosixPath(source_path).name
    if base in LAUNCHERS:
        return "launcher"
    if base.startswith("LICENSE") or destination.endswith("LICENSE"):
        return "license"
    return "vendor"


def _edge(kind, source, target, lock, *, pin=None, blob=None, sha256=None, auto=False, **extra):
    return {"kind": kind, "source": source, "target": target, "pin": pin, "blob": blob, "sha256": sha256,
            "lock": lock, "auto": auto, **extra}


def _lock_edges(name, lock, document, base):
    """Edges declared by one lock file; base is the subtree prefix its destinations are relative to."""
    edges, unmodeled = [], 0
    schema = (document or {}).get("schema")

    def at(path):
        return f"{base}/{path}" if base else path
    full = {"repository", "ref", "commit", "blob_sha", "sha256"}
    if schema == "vendor-lock/1":
        for item in document.get("files", []):
            if {"repository", "source", "destination"} <= set(item):
                edges.append(_edge(_kind(item["source"], item["destination"]), f"{short(item['repository'])}:{item['source']}",
                                   f"{name}:{at(item['destination'])}", f"{name}:{lock}", pin=item.get("commit"),
                                   blob=item.get("blob_sha"), sha256=item.get("sha256"), auto=full <= set(item)))
            elif {"path", "source", "consumers"} <= set(item):
                for consumer in item["consumers"]:
                    edges.append(_edge("shared-tools", f"{short(item['source'])}:{item['path']}", f"{short(consumer)}:",
                                       f"{name}:{lock}", sha256=item.get("sha256")))
            elif {"path", "source", "sha256"} <= set(item):  # a consumer's copy record of a hub original
                edges.append(_edge("copy", f"{short(item['source'])}:", f"{name}:{at(item['path'])}", f"{name}:{lock}",
                                   sha256=item["sha256"]))
            else:
                unmodeled += 1
    elif schema == "html-import-vendor/1":
        for item in document.get("sources", []):
            source = item.get("source_path") or item.get("source")
            if item.get("repository") and source and item.get("path"):
                edges.append(_edge(_kind(source, item["path"]), f"{short(item['repository'])}:{source}",
                                   f"{name}:{at(item['path'])}", f"{name}:{lock}", pin=item.get("commit"),
                                   sha256=item.get("sha256")))
            else:
                unmodeled += 1
    return edges, unmodeled


def _checkout_uses(text, line):
    """Parent files a pinned whole-repository checkout step is used for: '<path>/<file>' after its 'path:'."""
    lines = text.splitlines()
    start = line - 1
    while start > 0 and not re.match(r"\s*-\s", lines[start]):
        start -= 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and not (re.match(r"\s*-\s", lines[end]) and len(lines[end]) - len(lines[end].lstrip()) <= indent):
        end += 1
    path = next((m.group(1).strip("'\"").rstrip("/") for l in lines[start:end] if (m := re.match(r"\s+path:\s*(\S+)\s*$", l))), "")
    if path in ("", "."):
        return path, None  # checked out over the workspace: the whole parent is the dependency
    files = sorted(set(re.findall(re.escape(path) + r"/([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|cjs|mjs|js|json|sh|ya?ml))", text)))
    if not files and len(re.findall(re.escape(path) + r"(?![A-Za-z0-9_.-])", text)) > 1:
        return path, None  # the directory itself is passed on (an environment value, an argument)
    return path, files


def _adjacency(edges, prefixes):
    """Outgoing edges per node, plus implied ones: a file feeds its subtree prefix and its repository node."""
    out = {}
    for edge in edges:
        out.setdefault(edge["source"], []).append(edge)
    repo_nodes = {e["source"] for e in edges if e["source"].endswith(":")}
    nodes = {e["source"] for e in edges} | {e["target"] for e in edges}
    for node in sorted(nodes):
        repo, path = split(node)
        if not path:
            continue
        for prefix in prefixes.get(repo, ()):
            if path.startswith(prefix + "/") and node != f"{repo}:{prefix}/":
                out.setdefault(node, []).append({"kind": "implied", "source": node, "target": f"{repo}:{prefix}/"})
        if f"{repo}:" in repo_nodes:
            out.setdefault(node, []).append({"kind": "implied", "source": node, "target": f"{repo}:"})
    return out


def _consumer_of(node, subtrees):
    """The repository a node really belongs to: a file under a hub's subtree prefix is that child's file."""
    for prefix_node, child in subtrees.items():
        if node.startswith(prefix_node):
            return split(child)[0]
    return split(node)[0]


def build_graph(fleet, discovery, records=None, *, evidence=True, snapshot=None):
    """File-level dependency graph, repository-level mutual references and execution order (graphlib).

    evidence=True adds graph["evidence"]: tested_by / produced_by / derived_from / observed_in / verified_by /
    supports / contradicts / depends_on relations from the repositories' own records (fleet_records). They are
    walked breadth-first by impact and why, never topologically sorted: evidence graphs need not be DAGs.
    snapshot: a parent-audit/1 snapshot (ci_status) whose PR dependencies join as the "pr" group.
    """
    records = records or load_records(fleet)
    rows = {r["repository"]: r for r in discovery["repositories"]}
    readable = [r for r in discovery["repositories"] if r["category"] in ("target", "insufficient") and r["revision"]]
    edges, findings = [], []
    prefixes = prefixes_of(discovery)
    vendor_sync = _sibling("vendor_sync")
    for r in readable:
        name, rev = r["repository"], r["revision"]
        files = fleet.files(name, rev)
        workflow_paths = [p for p in sorted(files) if p.startswith(".github/workflows/") and p.count("/") == 2
                          and p.endswith((".yml", ".yaml"))]
        fleet.prefetch(name, rev, r["lock_files"] + workflow_paths)
        for lock in r["lock_files"]:
            base = next((p for p in sorted(prefixes.get(name, ())) if lock.startswith(p + "/")), "")
            try:
                document = fleet.json(name, rev, lock)
            except (ValueError, UnicodeDecodeError) as exc:
                findings.append({"repository": name, "kind": "lock-unreadable", "subject": lock, "detail": f"{lock}: {exc}"})
                continue
            found, unmodeled = _lock_edges(name, lock, document, base)
            edges.extend(found)
            if unmodeled:
                findings.append({"repository": name, "kind": "lock-rows-unmodeled", "subject": lock, "detail": f"{lock}: {unmodeled} row(s)"})
        workflows = {p: fleet.read(name, rev, p).decode("utf-8", "replace") for p in workflow_paths}
        for pin in vendor_sync.collect_workflow_pins(workflows):
            if name == PARENT and pin["kind"] == "workflow-checkout":
                continue  # the parent checking out its own older commit is a historical fixture, not a dependency
            if pin["kind"] == "workflow-uses":
                used = [pin["entry"]]
            else:
                where, used = _checkout_uses(workflows[pin["file"]], pin["line"])
                if used == []:
                    findings.append({"repository": name, "kind": "unused-parent-checkout", "subject": pin["file"],
                                     "detail": f"{pin['file']}:{pin['line']} pins the parent into {where} but no step uses it"})
                    continue
            for path in used or [""]:  # None: checked out over the workspace, the whole parent is the dependency
                if not any(e["kind"] == "workflow-pin" and e["source"] == f"{PARENT}:{path}" and e["pin"] == pin["commit"]
                           and e["target"] == f"{name}:{pin['file']}" for e in edges[-40:]):  # several steps, one pin
                    edges.append(_edge("workflow-pin", f"{PARENT}:{path}", f"{name}:{pin['file']}", f"{name}:{pin['file']}",
                                       pin=pin["commit"], line=pin.get("line")))
    for r in discovery["repositories"]:
        for hub, info in r["hubs"].items():
            # A hub that records its own head (the parent) records an earlier commit of itself, not a dependency.
            if hub != r["repository"] and rows.get(hub, {}).get("category") in ("target", "insufficient") \
                    and r["category"] != "excluded":
                edges.append(_edge("heads", f"{r['repository']}:", f"{hub}:child-heads.json", f"{hub}:child-heads.json",
                                   pin=info.get("sha")))
                if info.get("prefix"):
                    edges.append(_edge("subtree", f"{hub}:{info['prefix']}/", f"{r['repository']}:", f"{hub}:child-heads.json"))
    for tool in records["catalog"]["tools"]:
        edges.append(_edge("catalog", f"{short(tool['repository'])}:{tool['source']}", f"{PARENT}:vendor-catalog.json",
                           f"{PARENT}:vendor-catalog.json", pin=tool["commit"]))
    seen, disagree = {}, {}
    for e in list(edges):  # one dependency recorded in two locks counts once; a disagreement is a finding
        key = (e["kind"], e["source"], e["target"])
        if key in seen and e["kind"] in UPDATE_KINDS:
            first = seen[key]
            if (first["pin"], first["sha256"]) != (e["pin"], e["sha256"]):
                disagree.setdefault((split(e["target"])[0], first["lock"], e["lock"]), []).append(split(e["target"])[1])
            edges.remove(e)
        seen.setdefault(key, e)
    for (repo, a, b), paths in sorted(disagree.items()):
        findings.append({"repository": repo, "kind": "locks-disagree", "subject": f"{a}|{b}",
                         "detail": f"{a} and {b} pin {len(paths)} shared file(s) differently: {', '.join(sorted(paths)[:4])}"})
    originals = {}
    for e in edges:
        if e["kind"] == "shared-tools":
            originals.setdefault(split(e["source"])[0], set()).add(split(e["source"])[1])
    for e in [x for x in edges if x["kind"] == "copy"]:
        repo, path = split(e["target"])
        match = sorted(o for o in originals.get(split(e["source"])[0], ()) if path == o or path.endswith("/" + o))
        if match:
            e.update(kind="vendor", source=f"{split(e['source'])[0]}:{match[-1]}")
        else:
            edges.remove(e)
            findings.append({"repository": repo, "kind": "copy-record-unmatched", "subject": path,
                             "detail": f"{e['lock']}: {path} matches no original in {split(e['source'])[0]}:shared-tools.lock.json"})
    # A hub's shared-tools.lock.json names the consumers of each original; the consumers' locks pin it.
    subtrees = {e["source"]: e["target"] for e in edges if e["kind"] == "subtree"}
    registered = {(short(c["repository"]), x) for c in records["registry"]["consumers"] for entry in c["entries"]
                  for x in (entry.get("destination"), entry.get("license")) if x}
    for e in edges:  # which copies the parent's vendor-consumers.json also registers (the basis besides the lock)
        if e["kind"] in UPDATE_KINDS:
            consumer = _consumer_of(e["target"], subtrees)
            path = split(e["target"])[1]
            prefix = next((p for p, child in subtrees.items() if split(child)[0] == consumer and e["target"].startswith(p)), None)
            e["registered"] = (consumer, path[len(split(prefix)[1]):] if prefix else path) in registered
    declared = {(split(e["target"])[0], e["source"]) for e in edges if e["kind"] == "shared-tools"}
    originals = {e["source"] for e in edges if e["kind"] == "shared-tools"}
    pinned = {}
    for e in edges:
        if e["kind"] in UPDATE_KINDS and e["source"] in originals:
            consumer = _consumer_of(e["target"], subtrees)
            if consumer != split(e["source"])[0]:
                pinned.setdefault((consumer, e["source"]), e["lock"])
    for (consumer, source), lock in sorted(pinned.items()):
        if (consumer, source) not in declared:
            findings.append({"repository": consumer, "kind": "shared-tools-undeclared", "subject": source,
                             "detail": f"{lock} pins {source}, but {split(source)[0]}:shared-tools.lock.json does not list {consumer}"})
    for consumer, source in sorted(declared):
        if (consumer, source) not in pinned and rows.get(consumer, {}).get("category") in ("target", "insufficient"):
            findings.append({"repository": consumer, "kind": "shared-tools-unpinned", "subject": source,
                             "detail": f"listed as a consumer of {source}, but no lock row pins it"})
    findings += check_shapes(edges, discovery)
    adjacency = _adjacency(edges, prefixes)
    sorter = graphlib.TopologicalSorter()
    for node, outgoing in adjacency.items():
        sorter.add(node)
        for edge in outgoing:
            sorter.add(edge["target"], node)
    cycles, order = [], []
    try:
        order = list(sorter.static_order())
    except graphlib.CycleError as exc:
        cycles.append(list(exc.args[1]))
    pairs = {}
    for e in edges:
        a, b = split(e["source"])[0], split(e["target"])[0]
        if a != b:
            pairs.setdefault((a, b), set()).add(e["kind"])
    code = CODE_KINDS
    # Repository-level mutual references are information, not errors: "code" when both directions carry code.
    mutual = sorted(({"repositories": [a, b], "a_to_b": sorted(pairs[(a, b)]), "b_to_a": sorted(pairs[(b, a)]),
                      "kind": "code" if pairs[(a, b)] & code and pairs[(b, a)] & code else "record"}
                     for (a, b) in pairs if a < b and (b, a) in pairs), key=lambda m: (m["kind"], m["repositories"]))
    kinds = {}
    for e in edges:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    graph = {"edges": edges, "kinds": dict(sorted(kinds.items())), "findings": findings, "mutual": mutual,
             "cycles": cycles, "order": order, "prefixes": {k: sorted(v) for k, v in sorted(prefixes.items())},
             "nodes": len({n for e in edges for n in (e["source"], e["target"])})}
    if evidence:
        graph["evidence"] = collect_evidence(fleet, discovery, edges, records, snapshot=snapshot)
    return graph


def collect_evidence(fleet, discovery, edges, records, *, snapshot=None):
    """fleet_records.collect, limited to the public inventory: a relation naming any other repository is dropped
    and only counted (a corpus may name a repository that has since become private or been archived)."""
    found = _sibling("fleet_records").collect(fleet, discovery, edges, registry=records["registry"], snapshot=snapshot)
    known = {r["repository"] for r in discovery["repositories"] if r["category"] != "excluded"}
    kept, outside = [], {}
    for e in found["relations"]:
        ends = {split(e["source"])[0], split(e["target"])[0]}
        if ends <= known:
            kept.append(e)
        else:
            outside[e["lock"]] = outside.get(e["lock"], 0) + 1
    found["relations"] = kept
    found["outside_inventory"] = dict(sorted(outside.items()))
    found["kinds"] = {k: sum(e["kind"] == k for e in kept) for k in sorted({e["kind"] for e in kept})}
    found["findings"] = [f for f in found["findings"] if f["repository"] in known] + [
        f for f in check_shapes(kept, discovery) if f["kind"] == "shape"]
    found["cycles"] = [c for c in found["cycles"] if {split(n)[0] for n in c} <= known]
    found["coverage"] = {repo: cover for repo, cover in found["coverage"].items() if repo in known}
    return found


def node_class(node):
    """Ontology class of a node string ('Repo:', 'Repo:prefix/', 'Repo:path')."""
    _, path = split(node)
    if not path:
        return "Repository"
    if path.startswith("#"):
        return "PullRequest"
    if path.startswith("actions/runs/"):
        return "Run"
    if "#" in path:
        return "Entry"
    if path.endswith("/"):
        return "Directory"
    if PurePosixPath(path).name in RECORD_NAMES:
        return "Record"
    if path.startswith(".github/workflows/"):
        return "Workflow"
    return "File"


def check_shapes(edges, discovery):
    """Findings where the graph breaks its own codebook (a small SHACL-like check, no framework)."""
    findings, sources = [], {}
    known = {r["repository"] for r in discovery["repositories"]}
    for e in edges:
        _, _, domain, range_, *_ = RELATIONS[e["kind"]]
        for end, allowed in ((e["source"], domain), (e["target"], range_)):
            if node_class(end) not in allowed.split("|") and not (node_class(end) == "Record" and "File" in allowed):
                findings.append({"repository": split(e["target"])[0], "kind": "shape", "subject": f"{e['kind']}:{end}",
                                 "detail": f"{e['kind']} expects {allowed} at {end} ({node_class(end)}), declared in {e['lock']}"})
        for end in (e["source"], e["target"]):
            if split(end)[0] not in known:
                findings.append({"repository": split(end)[0], "kind": "unknown-repository", "subject": split(end)[0],
                                 "detail": f"{e['lock']} names a repository no registry, hub or checkout knows"})
        if e["kind"] in UPDATE_KINDS:
            sources.setdefault(e["target"], set()).add(e["source"])
    for target, origins in sorted(sources.items()):
        if len(origins) > 1:  # a copy has exactly one origin
            findings.append({"repository": split(target)[0], "kind": "two-sources", "subject": target,
                             "detail": f"{split(target)[1]} is copied from {' and '.join(sorted(origins))}"})
    names = {}
    for r in discovery["repositories"]:
        if r.get("repository_id"):
            names.setdefault(r["repository_id"], set()).add(r["repository"])
    for repository_id, same in sorted(names.items()):
        if len(same) > 1:  # renamed but recorded under both names: one repository, two nodes
            findings.append({"repository": sorted(same)[0], "kind": "same-repository-two-names", "subject": str(repository_id),
                             "detail": f"repository_id {repository_id} is recorded as {' and '.join(sorted(same))}"})
    unique = {}
    for f in findings:
        unique.setdefault((f["kind"], f["repository"], f["subject"]), f)
    return list(unique.values())


def parse_node(value):
    """'Repo:path', 'Repo:' (whole repository) or a parent path such as 'py_launch.cjs'."""
    value = str(value).strip()
    if ":" not in value:
        return f"{PARENT}:{value}"
    repo, path = split(value)
    return f"{short(repo)}:{path}"


BASIS_FIELDS = ("lock", "method", "basis", "pin", "blob", "sha256", "compare", "state", "registered", "line", "observed_at")


def impact(graph, changed, *, tests=None):
    """Everything downstream of the changed nodes, with depth, the chain that reaches it and the edge's basis.

    Walks dependency edges and evidence relations breadth-first (no DAG assumed). A file inside a directory that
    a relation names (a ledger whose data file is unresolved) reaches that relation too. tests(repo, paths) may
    add tested_by for reached files the graph has no test link for yet (the CLI passes one; JSON-LD has none).
    The result's "view" answers: 変更の影響先 / 判断の根拠 / 再テストが必要な対象 / 再検証が必要な記録 / 未登録.
    """
    relations = (graph.get("evidence") or {}).get("relations", [])
    prefixes = {k: set(v) for k, v in graph["prefixes"].items()}
    adjacency = _adjacency(graph["edges"] + relations, prefixes)
    subtrees = {e["source"]: e["target"] for e in graph["edges"] if e["kind"] == "subtree"}
    directories = sorted(n for n in adjacency if n.endswith("/") and split(n)[1] and n not in subtrees)
    start = [parse_node(x) for x in changed]
    best, candidates = {}, {}
    queue = [(node, 0, [node]) for node in start]
    while queue:
        node, depth, chain = queue.pop(0)
        repo, path = split(node)
        outgoing = list(adjacency.get(node, ()))
        for prefix_node, child in subtrees.items():  # a file inside a subtree prefix is also that child's file
            if path and node.startswith(prefix_node) and node != prefix_node:
                outgoing.append({"kind": "subtree", "target": f"{split(child)[0]}:{node[len(prefix_node):]}"})
        if path and not path.endswith("/"):
            outgoing += [e for d in directories if node.startswith(d) for e in adjacency[d]]
        if not path and node in start:  # a changed repository stands for every file it holds
            outgoing += [e for n, es in adjacency.items() if n.startswith(node) and n != node for e in es]
        for edge in outgoing:
            target = edge["target"]
            if target in start or target in best:
                continue
            if edge.get("basis") == "hypothesis":  # a candidate is shown, never walked: it decides no reverification
                candidates.setdefault(target, {"node": target, "kind": edge["kind"], "from": node, "record": edge.get("lock"),
                                               "method": edge.get("method"), "pin": edge.get("pin")})
                continue
            best[target] = {"node": target, "repository": split(target)[0], "depth": depth + 1, "kind": edge["kind"],
                            "group": RELATIONS.get(edge["kind"], ("",) * 5)[4] or "code", "via": chain + [target],
                            "basis": {k: edge[k] for k in BASIS_FIELDS if edge.get(k) is not None}}
            queue.append((target, depth + 1, chain + [target]))
    linked = {e["source"] for e in relations if e["kind"] == "tested_by"}
    if tests is not None:  # tested_by for reached files that no relation covers yet
        wanted = {}
        for r in list(best.values()) + [{"node": n, "depth": 0, "via": [n], "kind": "changed"} for n in start]:
            if node_class(r["node"]) in ("File", "Record") and r["node"] not in linked and r["kind"] != "tested_by":
                wanted.setdefault(split(r["node"])[0], {})[split(r["node"])[1]] = r
        for repo, paths in sorted(wanted.items()):
            for path, test, method in tests(repo, sorted(paths)):
                target = f"{repo}:{test}"
                linked.add(f"{repo}:{path}")
                if target not in best and target not in start:
                    origin = paths[path]
                    best[target] = {"node": target, "repository": repo, "depth": origin["depth"] + 1, "kind": "tested_by",
                                    "group": "test", "via": origin["via"] + [target],
                                    "basis": {"lock": target, "method": method,
                                              "basis": "observed" if method in ("ast-import", "workflow") else "derived"}}
    rows = sorted(best.values(), key=lambda r: (r["depth"], r["node"].casefold()))
    repos = {}
    for r in rows:
        if r["kind"] != "implied":
            entry = repos.setdefault(r["repository"], {"direct": False, "indirect": False})
            entry["direct" if r["depth"] == 1 else "indirect"] = True
    known = set(adjacency) | {e["target"] for e in graph["edges"] + relations}
    result = {"changed": start, "rows": rows, "repositories": dict(sorted(repos.items())), "tested": sorted(linked & (set(best) | set(start))),
              "tests": sorted({e["target"] for e in relations if e["kind"] == "tested_by"} & set(best)),
              "candidates": [candidates[k] for k in sorted(candidates) if k not in best],
              "unknown": [n for n in start if n not in known and not any(n.startswith(p) for p in subtrees)
                          and not any(n.startswith(d) for d in directories)
                          and not (split(n)[1] == "" and any(k.startswith(n) for k in known))]}
    result["view"] = impact_view(result)
    return result


def why(edges, node, *, depth=3, limit=200):
    """判断の根拠 — the basis of a node: inbound relations, breadth-first up to depth, each with its record and evidence."""
    inbound = {}
    for e in edges:
        inbound.setdefault(e["target"], []).append(e)
    rows, seen, queue = [], {node}, [(node, 0)]
    while queue and len(rows) < limit:
        current, level = queue.pop(0)
        if level >= depth:
            continue
        for e in sorted(inbound.get(current, ()), key=lambda e: (e["kind"], e["source"])):
            rows.append(dict(e, depth=level + 1))
            if e["source"] not in seen:
                seen.add(e["source"])
                queue.append((e["source"], level + 1))
    return {"node": node, "rows": rows[:limit], "truncated": len(rows) > limit}


def impact_view(reach, *, limit=200):
    """The impact as the four answers the dashboard and CLI show, each row with the record it rests on."""
    rows = [r for r in reach["rows"] if r["kind"] not in ("implied",)]
    affected = {}
    for r in rows:
        entry = affected.setdefault(r["repository"], {"repository": r["repository"], "direct": False, "groups": set(), "nodes": 0})
        entry["direct"] |= r["depth"] == 1
        entry["groups"].add(r["group"])
        entry["nodes"] += 1
    tested = {r["via"][-2] for r in rows if r["kind"] == "tested_by"} | set(reach.get("tested", ()))
    tests = {r["node"] for r in rows if r["kind"] == "tested_by"} | set(reach.get("tests", ()))
    retest = [{"test": r["node"], "for": r["via"][-2], "method": r["basis"].get("method"), "basis": r["basis"].get("basis")}
              for r in rows if r["kind"] == "tested_by"]
    recheck = [{"record": r["node"], "kind": r["kind"], "via": r["via"][-2], "compare": r["basis"].get("compare"),
                "state": r["basis"].get("state")}
               for r in rows if r["group"] in ("provenance", "evidence", "record") and node_class(r["node"]) in ("Entry", "Record", "File")
               and r["kind"] != "tested_by"]
    unregistered = sorted({r["node"] for r in rows if node_class(r["node"]) == "File" and r["group"] == "code"
                           and r["node"] not in tested and r["node"] not in tests and ".github/workflows/" not in r["node"]}
                          | {n for n in reach["changed"] if node_class(n) == "File" and n not in tested
                             and not any(r["via"][0] == n and r["kind"] == "tested_by" for r in rows)})
    basis = [{"node": r["node"], "kind": r["kind"], "group": r["group"], "from": r["via"][-2], **r["basis"]} for r in rows]
    return {"affected": [dict(v, groups=sorted(v["groups"])) for _k, v in sorted(affected.items())],
            "basis": basis[:limit], "retest": retest[:limit], "recheck": recheck[:limit],
            "unregistered": unregistered[:limit], "unknown": reach["unknown"], "candidates": reach.get("candidates", [])[:limit],
            "truncated": {k: max(0, n - limit) for k, n in (("basis", len(basis)), ("retest", len(retest)),
                                                             ("recheck", len(recheck)), ("unregistered", len(unregistered)))}}


# ---------------------------------------------------------------- 3. update plan (dry-run first)

def _parent_commit(fleet):
    done = fleet.run(fleet.parent_root, "rev-parse", "--verify", "-q", "HEAD")
    return done.stdout.decode().strip() if done.returncode == 0 else None


def _step(steps, key, **fields):
    step = steps.setdefault(key, {"id": key, "deps": set(), "reasons": [], "auto": False, "argv": None, **fields})
    return step


def _same(pin, rev, parent_head):
    """same | different | unknown for a recorded commit against the source's current one (no age, no verdict)."""
    current = parent_head if rev == WORKTREE else rev
    return "unknown" if not pin or not current else "same" if pin == current else "different"


def _facets(row, content, commit, state):
    """Separate answers a row status used to blend: content and commit comparison, evidence state, compatibility.

    content: match | different | unknown | not_applicable (bytes of the copy against its source)
    commit: same | different | unknown | not_applicable (recorded commit against the source's current commit)
    state: success | failure | pending | not_collected | unverifiable | not_applicable (fleet_records.STATES)
    compatibility stays not_assessed here: only a test run can say a different version still works.
    """
    row.update(content=content, commit=commit, state=state,
               compatibility="not_assessed" if row["kind"] in (*UPDATE_KINDS, "workflow-pin", "catalog") else "not_applicable")


def plan_updates(fleet, graph, discovery, *, changed=None, python="python"):
    """Ordered steps for vendored copies, pins and records that differ from their source.

    Identical content is skipped even when the source commit moved. Steps form a graphlib
    DAG grouped into waves: vendor → regenerate → verify per repository, then hub records.
    Nothing runs here; ``apply_plan`` runs only the automatic steps.
    """
    selected = None
    if changed:
        reach = impact(graph, changed)
        selected = set(reach["changed"]) | {r["node"] for r in reach["rows"]}
    repos = {r["repository"]: r for r in discovery["repositories"]}
    parent_head = _parent_commit(fleet)
    order = graph["order"] or sorted({n for e in graph["edges"] for n in (e["source"], e["target"])})
    position = {node: i for i, node in enumerate(order)}
    incoming = {}
    for e in graph["edges"]:
        if e["kind"] in UPDATE_KINDS:
            incoming.setdefault(e["target"], []).append(e)

    def current(node):
        repo, path = split(node)
        rev = fleet.revision(repo) if repos.get(repo, {}).get("category") in ("target", "insufficient") else None
        return rev, (fleet.blob(repo, rev, path) if rev and path else None)
    latest = {}
    for node in order:  # the blob a node will hold after the plan: a vendored copy takes its source's
        if node in incoming:
            latest[node] = latest.get(incoming[node][0]["source"]) or current(incoming[node][0]["source"])[1]
        elif split(node)[1]:
            latest[node] = None  # computed lazily from the repository itself
    rows = []
    for e in sorted(graph["edges"], key=lambda e: (position.get(e["target"], 0), e["target"], e["source"])):
        if e["kind"] == "subtree" or (selected is not None and (e["source"] not in selected or e["kind"] == "heads")):
            continue  # with --changed, hub records follow from the verify steps instead of the current drift
        src_repo, src_path = split(e["source"])
        dst_repo, dst_path = split(e["target"])
        rev, blob = current(e["source"])
        row = {"id": f"{e['kind']}:{e['target']}<-{e['source']}", "kind": e["kind"], "repository": dst_repo,
               "source": e["source"], "target": e["target"], "lock": e["lock"], "pin": e.get("pin"),
               "source_revision": rev, "checked_at": now(), "status": "skipped", "reason": ""}
        if e.get("registered") is not None:
            row["registered"] = e["registered"]
        if repos.get(src_repo, {}).get("category") == "excluded":
            row.update(status="skipped", reason="source excluded: " + "; ".join(repos[src_repo]["reasons"]))
            _facets(row, "not_applicable", "not_applicable", "not_applicable")
        elif rev is None:
            reasons = repos.get(src_repo, {}).get("reasons") or [fleet.unavailable.get(src_repo, "source unavailable")]
            row.update(status="unavailable", reason="source not readable: " + "; ".join(reasons))
            _facets(row, "unknown", "unknown", "not_collected")
        elif e["kind"] in UPDATE_KINDS:
            upstream = incoming.get(e["source"], [])
            expected = latest.get(e["source"]) or blob
            _facets(row, "unknown", _same(e.get("pin"), rev, parent_head), "pending")
            if expected is None:
                row.update(status="failure", reason="source path removed at " + ("worktree" if rev == WORKTREE else rev[:12])
                           + "; the pinned commit still has it (re-point or drop the entry)", state="failure")
            elif e.get("blob") is None:
                row.update(status="skipped", reason="sha256-only record; checked by its own tool", state="unverifiable")
            elif expected == e["blob"]:
                moved = e.get("pin") and rev not in (WORKTREE, e["pin"]) and not upstream
                row.update(status="skipped", reason="same content" + (f" (source commit moved {e['pin'][:7]}→{rev[:7]})" if moved else ""),
                           content="match", state="success")
            else:
                row["content"] = "different"
                via = e["source"] if upstream and latest.get(e["source"]) != blob else None
                uncommitted = rev == WORKTREE and parent_head and fleet.blob_at(PARENT, parent_head, src_path) != blob
                row.update(status="updated", reason=f"blob {e['blob'][:7]}→{expected[:7]}"
                           + (f" once {via} is updated" if via else "") + (" (uncommitted in the parent)" if uncommitted else ""),
                           upstream=via, uncommitted=bool(uncommitted))
        elif e["kind"] == "workflow-pin":
            commit = _same(e.get("pin"), WORKTREE, parent_head)
            if not src_path:
                same = e["pin"] == parent_head or not parent_head
                row.update(status="skipped" if same else "updated",
                           reason="whole-repository checkout pin" + ("" if same else f" {e['pin'][:7]}, parent HEAD {parent_head[:7]}"))
                _facets(row, "not_applicable", commit, "success" if same else "pending")
            else:
                pinned = fleet.blob_at(PARENT, e["pin"], src_path)
                if pinned is None:
                    row.update(status="unavailable", reason=f"parent commit {e['pin'][:7]} not available (full clone or --fetch)")
                    _facets(row, "unknown", commit, "not_collected")
                elif pinned == blob:
                    row.update(status="skipped", reason="same content at the pin")
                    _facets(row, "match", commit, "success")
                else:
                    row.update(status="updated", reason=f"pinned file changed since {e['pin'][:7]}")
                    _facets(row, "different", commit, "pending")
        elif e["kind"] == "catalog":
            pinned = fleet.blob_at(src_repo, e["pin"], src_path)
            commit = _same(e.get("pin"), rev, parent_head)
            if blob is None:
                row.update(status="failure", reason="catalog source missing at main")
                _facets(row, "unknown", commit, "failure")
            elif pinned is None:
                row.update(status="unavailable", reason=f"catalog commit {e['pin'][:7]} not available (full clone or --fetch)")
                _facets(row, "unknown", commit, "not_collected")
            elif pinned == blob:
                row.update(status="skipped", reason="same content as the recommended commit")
                _facets(row, "match", commit, "success")
            else:
                row.update(status="updated", reason=f"recommended {e['pin'][:7]} differs from main {_brief(rev)}")
                _facets(row, "different", commit, "pending")
        elif e["kind"] == "heads":
            if rev == WORKTREE or rev == e["pin"]:
                row.update(status="skipped", reason="recorded head is current")
                _facets(row, "not_applicable", "same", "success")
            else:
                row.update(status="updated", reason=f"recorded {str(e['pin'])[:7]}, main {rev[:7]}")
                _facets(row, "not_applicable", "different", "pending")
        elif e["kind"] == "shared-tools":
            row.update(status="skipped", reason="declared consumer (sha256 record)")
            _facets(row, "unknown", "not_applicable", "unverifiable")
        rows.append(row)
    steps = {}
    subtree_child = {split(child)[0]: split(prefix_node) for prefix_node, child in
                     ((e["source"], e["target"]) for e in graph["edges"] if e["kind"] == "subtree")}
    for r in rows:
        if r["status"] != "updated":
            continue
        dst_repo, dst_path = split(r["target"])
        hub, prefix = subtree_child.get(dst_repo, (None, ""))
        if r["kind"] in UPDATE_KINDS and hub and f"{prefix}{split(r['lock'])[1]}" in repos.get(hub, {}).get("lock_files", []):
            # A subtree child receives this copy with the hub's subtree push, not with its own update.
            sub = _step(steps, f"regenerate:{dst_repo}:subtree", phase="regenerate", repository=dst_repo,
                        argv=[python, "-S", "<tools>/gh_ops.py", "subtree-push", "--prefix", prefix.rstrip("/"),
                              "--remote", f"https://github.com/{OWNER}/{dst_repo}", "--branch", "sync/fleet-update"])
            sub["deps"].add(f"verify:{hub}")
            sub["reasons"].append(f"{dst_path}: {r['reason']} (arrives from {hub}:{prefix})")
            verify = _step(steps, f"verify:{dst_repo}", phase="verify", repository=dst_repo, auto=True, related=set())
            verify["deps"].add(sub["id"])
            verify["related"].add(PurePosixPath(dst_path).stem)
            if "vendor_sync check + tests that mention the updated files" not in verify["reasons"]:
                verify["reasons"].append("vendor_sync check + tests that mention the updated files")
            continue
        if r["kind"] in UPDATE_KINDS:
            src_repo = split(r["source"])[0]
            lock_repo, lock_path = split(r["lock"])
            base = next((p for p in graph["prefixes"].get(lock_repo, ()) if lock_path.startswith(p + "/")), "")
            key = f"vendor:{dst_repo}:{lock_path}:{src_repo}"
            edge = next(x for x in graph["edges"] if x["target"] == r["target"] and x["source"] == r["source"])
            step = _step(steps, key, phase="vendor", repository=dst_repo, lock=lock_path, base=base, entries=[], auto=True,
                         source_repository=src_repo, to_ref=None)
            step["entries"].append(dst_path[len(base) + 1:] if base else dst_path)
            step["reasons"].append(f"{dst_path}: {r['reason']}")
            step["auto"] = step["auto"] and edge["auto"]
            if r.get("upstream"):
                upstream_steps = [k for k, s in steps.items() if s.get("phase") == "vendor" and any(
                    f"{s['repository']}:{(s['base'] + '/' if s['base'] else '') + x}" == r["upstream"] for x in s["entries"])]
                # Same repository: wait for the first copy's commit. Another repository: wait for it to verify and merge.
                step["deps"].update(upstream_steps if src_repo == dst_repo else [f"verify:{src_repo}"])
                step["auto"] = False
                step["to_ref"] = "main"
                step["reasons"].append(f"after {src_repo} commits its own update")
            elif r.get("uncommitted"):
                step["auto"] = False
                step["to_ref"] = "main"
                step["reasons"].append("after the parent commits and merges the change")
            elif step["to_ref"] is None:
                step["to_ref"] = parent_head if r["source_revision"] == WORKTREE else r["source_revision"]
            verify = _step(steps, f"verify:{dst_repo}", phase="verify", repository=dst_repo, auto=True, related=set())
            verify["deps"].add(key)
            verify["related"].add(PurePosixPath(dst_path).stem)
            if "vendor_sync check + tests that mention the updated files" not in verify["reasons"]:
                verify["reasons"].append("vendor_sync check + tests that mention the updated files")
        elif r["kind"] == "workflow-pin":
            step = _step(steps, f"regenerate:{dst_repo}:workflow-pins", phase="regenerate", repository=dst_repo,
                         argv=[python, "-S", "<tools>/tools/parent_pin_check.py", "--root", f"<checkouts>/{dst_repo}"])
            step["reasons"].append(f"{dst_path}: {r['reason']}")
        elif r["kind"] == "catalog":
            step = _step(steps, "record:catalog", phase="record", repository=PARENT,
                         argv=[python, "-S", "pilot/vendor_dispatch.py", "--resolve-ref", "main", "--record"])
            step["reasons"].append(f"{r['source']}: {r['reason']}")
        elif r["kind"] == "heads":
            hub = dst_repo
            step = _step(steps, f"record:{hub}:child-heads", phase="record", repository=hub,
                         argv=list(PARENT_REFRESH) if hub == PARENT else None)
            step["reasons"].append(f"{split(r['source'])[0]}: {r['reason']}")
    subtrees = {e["source"]: e["target"] for e in graph["edges"] if e["kind"] == "subtree"}
    for key, step in list(steps.items()):  # a verified repository changes its hub records and subtree copies
        if step["phase"] != "verify":
            continue
        repo = step["repository"]
        for hub in repos.get(repo, {}).get("hubs", {}):
            record = _step(steps, f"record:{hub}:child-heads", phase="record", repository=hub,
                           argv=list(PARENT_REFRESH) if hub == PARENT else None)
            record["deps"].add(key)
            record["reasons"].append(f"{repo}: refresh after its update merges")
        touched = {f"{s['repository']}:{(s['base'] + '/' if s['base'] else '') + x}" for s in steps.values()
                   if s["phase"] == "vendor" and s["repository"] == repo for x in s["entries"]}
        for prefix_node, child in subtrees.items():
            hub, prefix = split(prefix_node)
            if hub == repo and any(t.startswith(prefix_node) for t in touched):
                name = split(child)[0]
                sub = _step(steps, f"regenerate:{name}:subtree", phase="regenerate", repository=name,
                            argv=[python, "-S", "<tools>/gh_ops.py", "subtree-push", "--prefix", prefix.rstrip("/"),
                                  "--remote", f"https://github.com/{OWNER}/{name}", "--branch", "sync/fleet-update"])
                sub["deps"].add(key)
                sub["reasons"].append(f"{prefix_node} changed")
    for step in steps.values():
        if step["phase"] == "vendor":
            root = "<parent>" if step["repository"] == PARENT else f"<checkouts>/{step['repository']}"
            root += f"/{step['base']}" if step["base"] else ""
            manifest = step["lock"][len(step["base"]) + 1:] if step["base"] else step["lock"]
            step["argv"] = [python, "-S", "<tools>/vendor_sync.py", "update", "--root", root, "--manifest", manifest]
            for entry in sorted(set(step["entries"])):
                step["argv"] += ["--only", entry]
            step["argv"] += ["--to-ref", step["to_ref"] or "main"]
            step["entries"] = sorted(set(step["entries"]))
        elif step["phase"] == "verify":
            step["related"] = sorted(step["related"])
            step["argv"] = [python, "-S", "<tools>/vendor_fleet_plan.py", "test", "--repos", step["repository"],
                            "--checkouts", "<checkouts>"] + [x for stem in step["related"] for x in ("--related", stem)]
        step["deps"] = sorted(d for d in step["deps"] if d in steps)
        step["reasons"] = list(dict.fromkeys(step["reasons"]))
    sorter = graphlib.TopologicalSorter({k: s["deps"] for k, s in steps.items()})
    sorter.prepare()
    waves = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        waves.append(ready)
        sorter.done(*ready)
    for index, wave in enumerate(waves, 1):
        for key in wave:
            steps[key]["wave"] = index
    return {"rows": rows, "steps": [steps[k] for wave in waves for k in wave], "waves": waves,
            "counts": {s: sum(r["status"] == s for r in rows) for s in STATUSES}, "changed": list(changed or []),
            "dry_run": True}


# ---------------------------------------------------------------- 4. tests across repositories

def _test_file(path, pattern):
    parts = path.split("/")
    return (path.endswith(".py") and fnmatch.fnmatch(parts[-1], pattern) and not SKIP_PARTS & set(parts[:-1])
            and any(p in ("tests", "test") for p in parts[:-1]))


def _classify(source, local_names):
    """('unittest' | 'pytest', needs_packages) from a test module's syntax tree, without importing it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "pytest", True
    imported, testcase = set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                label = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                testcase |= label.endswith("TestCase")
    functions = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test") for n in tree.body)
    runner = "unittest" if testcase and "pytest" not in imported and not functions else "pytest"
    third_party = {n for n in imported if n not in sys.stdlib_module_names and n not in local_names and n != "__future__"}
    return runner, bool(third_party)


def discover_tests(fleet, repo, rev, *, pattern="test*.py", related=(), runner="all"):
    """Selected test files of one repository: unittest groups per directory, one pytest group."""
    files = fleet.files(repo, rev)
    # Names a test can import from the checkout; a src/ layout is importable only once the project is installed.
    importable = [p for p in files if not p.startswith("src/")]
    local = {PurePosixPath(p).stem for p in importable if p.endswith(".py")} | {part for p in importable for part in p.split("/")[:-1]}
    chosen = [p for p in sorted(files) if _test_file(p, pattern)]
    fleet.prefetch(repo, rev, chosen)
    groups, pytest_files = {}, []
    for path in chosen:
        text = fleet.read(repo, rev, path).decode("utf-8", "replace")
        if related and not any(stem in text for stem in related):
            continue
        kind, packages = _classify(text, local)
        if kind == "unittest" and runner in ("all", "unittest"):
            directory = str(PurePosixPath(path).parent)
            group = groups.setdefault(directory, {"runner": "unittest", "start": directory, "modules": [], "packages": False})
            group["modules"].append(PurePosixPath(path).stem)
            group["packages"] |= packages
        elif kind == "pytest" and runner in ("all", "pytest"):
            pytest_files.append(path)
    out = list(groups.values())
    if pytest_files:
        out.append({"runner": "pytest", "files": pytest_files, "packages": True})
    for group in out:
        if group["packages"] and "uv.lock" not in files:
            group["unavailable"] = "needs the repository environment, but there is no uv.lock"
    return {"repository": repo, "revision": rev, "groups": out, "matched": len(chosen),
            "config": [p for p in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "conftest.py", "uv.lock") if p in files]}


def _isolated(fleet, repo, rev, commands, *, timeout):
    """Run argv lists in a disposable detached worktree of repo at rev; per-command exit and output tails."""
    location = fleet.location(repo)
    rows = []
    with tempfile.TemporaryDirectory(prefix=f"fleet-{repo}-") as temporary:
        checkout = location if rev == WORKTREE else Path(temporary) / "checkout"
        if rev != WORKTREE:
            added = fleet.run(location, "worktree", "add", "--quiet", "--detach", str(checkout), rev)
            if added.returncode:
                return [{"status": "unavailable",
                         "reason": "worktree failed: " + added.stderr.decode("utf-8", "replace").strip()[-200:]}]
        try:
            for argv in commands:
                argv = [sys.executable if a == "{python}" else expand(fleet, a) for a in argv]
                started = time.monotonic()
                row = {"argv": argv}
                with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                    try:
                        done = subprocess.run(argv, cwd=checkout, env=clean_env(), stdin=subprocess.DEVNULL,
                                              stdout=out, stderr=err, timeout=timeout, check=False)
                        row["exit"] = done.returncode
                    except subprocess.TimeoutExpired:
                        row.update(exit=None, timeout=True)
                    except OSError as exc:
                        row.update(exit=None, spawn_error=type(exc).__name__)
                    for name, stream in (("stdout", out), ("stderr", err)):
                        size = stream.seek(0, 2)
                        stream.seek(max(0, size - 8192))
                        row[name] = stream.read().decode("utf-8", "replace")
                row["duration_s"] = round(time.monotonic() - started, 2)
                if rev != WORKTREE:
                    row["head_changed"] = fleet.run(checkout, "rev-parse", "HEAD").stdout.decode().strip() != rev
                rows.append(row)
        finally:
            if rev != WORKTREE:
                fleet.run(location, "worktree", "remove", "--force", str(checkout))
                fleet.run(location, "worktree", "prune")
    return rows


def expand(fleet, value):
    """Placeholders in planned commands: <tools> = this checkout's tools, <parent> = analysed parent, <checkouts>."""
    value = str(value).replace("<tools>", str(ROOT)).replace("<parent>", str(fleet.parent_root))
    return value.replace("<checkouts>", str(fleet.checkouts)) if fleet.checkouts else value


def _row(fleet, job, status, reason, **extra):
    shown = ["python" if a == "{python}" else fleet.display(a) for a in job.get("argv_display") or []]
    return {"id": job["id"], "kind": job["kind"], "repository": job["repository"], "status": status, "reason": reason,
            "revision": job.get("revision"), "checked_at": now(), "deps": list(job.get("deps", [])), "argv": shown, **extra}


def _summarize(fleet, job, ran):
    """One result row from the command rows of a job."""
    if not ran:
        return _row(fleet, job, "skipped", "nothing to run")
    if "status" in ran[0]:
        return _row(fleet, job, ran[0]["status"], ran[0]["reason"])
    duration = round(sum(r.get("duration_s", 0) for r in ran), 2)
    if job["kind"] == "check":
        bad = [r for r in ran if r.get("exit") != 0]
        if any(r.get("spawn_error") for r in bad):
            return _row(fleet, job, "unavailable", "could not start vendor_sync.py", duration_s=duration)
        if bad:
            return _row(fleet, job, "failure", "vendor_sync check: " + (bad[0].get("stderr") or "").strip()[-200:], duration_s=duration)
        return _row(fleet, job, "success", f"{len(ran)} lock(s) match their files", duration_s=duration)
    last = ran[-1]
    if last.get("spawn_error"):
        return _row(fleet, job, "unavailable", f"{last['spawn_error']}: cannot start {Path(last['argv'][0]).name}", duration_s=duration)
    if last.get("timeout"):
        return _row(fleet, job, "failure", "timeout", duration_s=duration)
    if last.get("head_changed"):
        return _row(fleet, job, "failure", "test changed the checked-out HEAD", duration_s=duration)
    summary = None
    for line in (last.get("stdout") or "").splitlines():
        if line.startswith(RESULT_MARK):
            summary = json.loads(line[len(RESULT_MARK):])
    if summary is None:
        text = (last.get("stdout") or "") + "\n" + (last.get("stderr") or "")
        found = [l for l in text.splitlines() if re.search(r"\d+ (passed|failed|errors?|skipped|deselected)|no tests ran|tests? collected", l)]
        tail = text.strip().splitlines()[-1:] or [""]
        summary = {"line": (found[-1] if found else tail[0]).strip("= ")[-200:]}
    if last["exit"] == 5:
        return _row(fleet, job, "skipped", "0 tests selected (not a pass)", summary=summary, duration_s=duration)
    if last["exit"] == 0:
        reason = f"{summary['run']} tests" if summary.get("run") else f"{summary['selected']} tests listed" \
            if "selected" in summary else summary.get("line", "")
        return _row(fleet, job, "success", reason, summary=summary, duration_s=duration)
    if "run" in summary:
        reason = (f"{summary.get('failures', 0)} failures, {summary.get('errors', 0)} errors, "
                  f"{len(summary.get('load_errors', []))} import errors of {summary.get('selected', 0)}")
    else:
        reason = summary.get("line") or f"exit {last['exit']}"
    text = (last.get("stdout") or "") + "\n" + (last.get("stderr") or "")
    first = re.search(r"^(?:E\s+)?((?:\w+\.)*\w*(?:Error|Exception)\b:.*)$", text, re.M)
    if first:  # the first error line says more than the counts
        reason += " — " + first.group(1).strip()[:160]
    return _row(fleet, job, "failure", reason, summary=summary, duration_s=duration)


def _missing_package(ran):
    """The first third-party module a stdlib-only run could not import, if that is why it failed."""
    for row in ran:
        for name in re.findall(r"No module named '([A-Za-z_][\w.]*)'", (row.get("stdout") or "") + (row.get("stderr") or "")):
            if name.split(".")[0] not in sys.stdlib_module_names:
                return name
    return None


def test_jobs(fleet, discovery, *, repos=None, pattern="test*.py", keywords=(), related=(), runner="all",
              list_only=False, python="{python}", checks=True):
    """Jobs per selected repository: one offline vendor check, then one job per test group depending on it."""
    jobs = {}
    wanted = {short(r) for r in repos} if repos else None
    prefixes = prefixes_of(discovery)
    for r in discovery["repositories"]:
        name = r["repository"]
        if r["category"] == "excluded" or not r["revision"] or (wanted is not None and name not in wanted):
            continue
        rev = r["revision"]
        tests = discover_tests(fleet, name, rev, pattern=pattern, related=related, runner=runner)
        deps = []
        verifiable = []
        for lock in (l for l in r["lock_files"] if l.endswith("vendor.lock.json")):
            document = fleet.json(name, rev, lock) or {}
            if document.get("schema") == "vendor-lock/1" and all("destination" in x for x in document.get("files", [])):
                verifiable.append(lock)
        if checks and verifiable and not list_only:
            commands = []
            for lock in verifiable:
                base = next((p for p in sorted(prefixes.get(name, ())) if lock.startswith(p + "/")), "")
                commands.append([python, "-S", "<tools>/vendor_sync.py", "check", "--root", base or ".",
                                 "--manifest", lock[len(base) + 1:] if base else lock])
            jobs[f"check:{name}"] = {"id": f"check:{name}", "kind": "check", "repository": name, "revision": rev,
                                     "deps": [], "commands": commands, "argv_display": commands[0]}
            deps = [f"check:{name}"]
        for group in tests["groups"]:
            if group["runner"] == "unittest":
                head = (["uv", "run", "--locked", "--quiet", "python", "-I"] if group["packages"] else [python, "-I", "-S"])
                argv = head + ["<tools>/vendor_fleet_plan.py", "unittest-worker", "--start", group["start"]]
                for module in group["modules"]:
                    argv += ["--module", module]
                for keyword in keywords:
                    argv += ["-k", keyword]
                argv += ["--list"] if list_only else []
                label = group["start"]
            else:
                argv = ["uv", "run", "--locked", "--quiet", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *group["files"]]
                argv += ["-k", " or ".join(keywords)] if keywords else []
                argv += ["--collect-only"] if list_only else []
                label = "pytest"
            job = {"id": f"test:{name}:{label}", "kind": "test", "repository": name, "revision": rev, "deps": deps,
                   "commands": [argv], "argv_display": argv, "runner": group["runner"], "unavailable": group.get("unavailable")}
            if group["runner"] == "unittest" and not group["packages"] and "uv.lock" in fleet.files(name, rev):
                # An import of a package hidden behind a local module or importlib re-runs in the repository environment.
                job["fallback"] = ["uv", "run", "--locked", "--quiet", "python", "-I"] + argv[3:]
            jobs[job["id"]] = job
        if not tests["groups"]:
            jobs[f"test:{name}"] = {"id": f"test:{name}", "kind": "test", "repository": name, "revision": rev, "deps": deps,
                                    "commands": [], "argv_display": [],
                                    "skip": (f"{tests['matched']} file(s) match {pattern!r}, none for --runner {runner}"
                                             + (f" that mention {', '.join(related)}" if related else "")) if tests["matched"]
                                    else f"no test file matches {pattern!r}"}
    return jobs


def execute(fleet, jobs, *, workers=4, timeout=900, run_job=None):
    """Run jobs in graphlib order, independent ones in parallel; a failed or held prerequisite blocks dependents."""
    def default(job):
        if job.get("skip"):
            return _row(fleet, job, "skipped", job["skip"], hold=job.get("hold", False))
        if job.get("unavailable"):
            return _row(fleet, job, "unavailable", job["unavailable"])
        ran = _isolated(fleet, job["repository"], job["revision"], job["commands"], timeout=timeout)
        row = _summarize(fleet, job, ran)
        missing = _missing_package(ran) if row["status"] == "failure" and job.get("fallback") else None
        if missing:
            again = dict(job, argv_display=job["fallback"])
            row = _summarize(fleet, again, _isolated(fleet, job["repository"], job["revision"], [job["fallback"]], timeout=timeout))
            row["reason"] += f" (re-run in the repository environment: {missing} is not in the standard library)"
        return row
    run_job = run_job or default
    sorter = graphlib.TopologicalSorter({k: [d for d in j["deps"] if d in jobs] for k, j in jobs.items()})
    sorter.prepare()
    results, running = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        while sorter.is_active():
            for key in sorted(sorter.get_ready()):
                job = jobs[key]
                held = [d for d in job["deps"] if d in results and (results[d].get("hold")
                        or results[d]["status"] not in ("success", "skipped", "updated"))]
                if held:
                    results[key] = _row(fleet, job, "blocked", "waits for " + ", ".join(f"{d} ({results[d]['status']})" for d in held))
                    sorter.done(key)
                else:
                    running[pool.submit(run_job, job)] = key
            if not running:
                continue
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                key = running.pop(future)
                try:
                    results[key] = future.result()
                except Exception as exc:  # a broken runner is reported, never counted as a pass
                    results[key] = _row(fleet, jobs[key], "unavailable", f"{type(exc).__name__}: {exc}"[:300])
                sorter.done(key)
    return [results[k] for k in sorted(results, key=lambda k: (jobs[k]["repository"].casefold(), k))]


def _in_place(fleet, timeout):
    """Run a job's commands in the repository's own checkout (it holds the applied, uncommitted update)."""
    def run(job):
        if job.get("skip"):
            return _row(fleet, job, "skipped", job["skip"])
        if job.get("unavailable"):
            return _row(fleet, job, "unavailable", job["unavailable"])
        return _summarize(fleet, job, _isolated(fleet, job["repository"], WORKTREE, job["commands"], timeout=timeout))
    return run


def apply_plan(fleet, planned, discovery, *, workers=2, timeout=900):
    """Run the automatic steps of a plan in local checkouts; manual and after-merge steps hold their dependents."""
    by_id = {s["id"]: s for s in planned["steps"]}
    jobs = {}
    for step in planned["steps"]:
        job = {"id": step["id"], "kind": "step", "phase": step["phase"], "repository": step["repository"],
               "deps": step["deps"], "argv_display": step.get("argv") or [], "revision": None}
        if not step["auto"]:
            job.update(skip="manual or after-merge step: " + "; ".join(step["reasons"])[:300], hold=True)
        elif step["repository"] != PARENT and fleet.checkout_path(step["repository"]) is None:
            job["unavailable"] = "needs a worktree clone under --checkouts"
        jobs[step["id"]] = job

    def run(job):
        if job.get("skip"):
            return _row(fleet, job, "skipped", job["skip"], hold=job.get("hold", False))
        if job.get("unavailable"):
            return _row(fleet, job, "unavailable", job["unavailable"])
        step = by_id[job["id"]]
        if step["phase"] == "vendor":
            argv = [sys.executable if a == "python" else expand(fleet, a) for a in step["argv"]]
            done = subprocess.run(argv, env=clean_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=timeout, check=False)
            if done.returncode:
                last = [l for l in done.stderr.splitlines() if l.strip()][-1:] or [f"exit {done.returncode}"]
                return _row(fleet, job, "failure", last[0][-300:])
            changed = json.loads(done.stdout or "{}").get("changed_paths", [])
            return _row(fleet, job, "updated" if changed else "skipped",
                        f"wrote {len(changed)} path(s)" if changed else "same content; nothing written", changed=changed)
        local = {"repositories": [dict(r, revision=WORKTREE) if r["repository"] == step["repository"] else r
                                  for r in discovery["repositories"]]}
        checks = execute(fleet, test_jobs(fleet, local, repos=[step["repository"]], related=step.get("related", ())),
                         workers=1, timeout=timeout, run_job=_in_place(fleet, timeout))
        worst = next((s for s in ("failure", "blocked", "unavailable") if any(r["status"] == s for r in checks)), None)
        return _row(fleet, job, worst or "success", "; ".join(f"{r['id']} {r['status']}" for r in checks)[:300], checks=checks)
    return execute(fleet, jobs, workers=workers, run_job=run)


def unittest_worker(argv=None):
    """Inside one checkout: load tests with unittest.TestLoader (names, patterns, -k) and print one summary line."""
    parser = argparse.ArgumentParser(prog="vendor_fleet_plan.py unittest-worker")
    parser.add_argument("--start", default="tests", help="test directory, put on sys.path after the checkout root")
    parser.add_argument("--module", action="append", default=[], help="names for TestLoader.loadTestsFromNames")
    parser.add_argument("--pattern", default="test*.py", help="TestLoader.discover pattern when no --module is given")
    parser.add_argument("-k", dest="keywords", action="append", default=[],
                        help="TestLoader.testNamePatterns with unittest -k semantics")
    parser.add_argument("--list", action="store_true", help="load and list test ids without running them")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    start = (root / args.start).resolve()
    if not start.is_relative_to(root) or not start.is_dir():
        parser.error("--start must be a directory inside the checkout")
    sys.path[:0] = [str(start), str(root)]
    loader = unittest.TestLoader()
    if args.keywords:
        loader.testNamePatterns = [k if "*" in k else f"*{k}*" for k in args.keywords]
    suite = loader.loadTestsFromNames(args.module) if args.module else \
        loader.discover(str(start), pattern=args.pattern, top_level_dir=str(start))

    def flatten(item):
        for test in item:
            yield from flatten(test) if isinstance(test, unittest.TestSuite) else [test]
    ids = [t.id() for t in flatten(suite)]
    payload = {"schema": "fleet-unittest/1", "start": args.start, "selected": len(ids),
               "load_errors": [e.strip().splitlines()[-1][:300] for e in loader.errors]}
    if args.list:
        payload.update(ids=ids)
        print(RESULT_MARK + json.dumps(payload, ensure_ascii=False), flush=True)
        return 1 if loader.errors else 0 if ids else 5
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    payload.update(run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses))
    payload["ok"] = result.wasSuccessful() and not loader.errors and result.testsRun > 0
    print(RESULT_MARK + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if payload["ok"] else 5 if result.testsRun == 0 and not loader.errors else 1


# ---------------------------------------------------------------- 5. one result for CLI, JSON, Markdown and Pages

def report(fleet, *, previous=None, changed=None, tests=None, listing=None, snapshot=None, evidence=True):
    """targets + graph + plan (+ test rows) with counts, staleness, the diff to previous and what remains.

    Check/test rows of a previous report that were not re-run are carried over only while
    their repository still has the same revision; otherwise they move to ``stale``.
    """
    records = load_records(fleet)
    discovery = discover(fleet, records, listing=listing)
    graph = build_graph(fleet, discovery, records, evidence=evidence, snapshot=snapshot)
    planned = plan_updates(fleet, graph, discovery, changed=changed)
    revisions = {r["repository"]: r["revision"] for r in discovery["repositories"]}
    rows = [dict(r, revision=revisions.get(r["repository"])) for r in planned["rows"]] + list(tests or [])
    ran = {r["id"] for r in rows}
    if previous and previous.get("schema") == REPORT_SCHEMA:
        for old in previous.get("rows", []):
            if old.get("kind") in ("check", "test") and old["id"] not in ran:
                rows.append(dict(old, carried_from=previous.get("generated_at")))
    current, stale = [], []
    for row in rows:
        head = revisions.get(row["repository"])
        moved = bool(row.get("revision") and head and row["revision"] != head and row["revision"] != WORKTREE)
        (stale if moved else current).append(dict(row, stale=moved, current_revision=head) if moved else row)
    result = {
        "schema": REPORT_SCHEMA, "generated_at": now(), "owner": OWNER, "parent_revision": _parent_commit(fleet),
        "parent_dirty": bool(fleet.run(fleet.parent_root, "status", "--porcelain").stdout.strip()),
        "mode": {"at": fleet.at, "fetch": fleet.fetch, "checkouts": bool(fleet.checkouts), "repos": sorted(fleet.only or [])},
        "targets": discovery, "graph": {k: v for k, v in graph.items() if k != "order"},
        "plan": {k: v for k, v in planned.items() if k != "rows"}, "rows": current, "stale": stale,
        "counts": {s: sum(r["status"] == s for r in current) for s in STATUSES},
        "cache": dict(fleet.stats), "unavailable": dict(sorted(fleet.unavailable.items())),
    }
    facts = compress_rows(current)
    result["facts"] = facts
    result["remaining"] = remaining(result, previous)
    raw, packed = len(json.dumps(current, ensure_ascii=False)), len(json.dumps(facts, ensure_ascii=False))
    shown = render({"remaining": result["remaining"]}, "markdown", sections=("remaining",))
    result["compression"] = {"rows": len(current), "facts": len(facts), "raw_bytes": raw, "fact_bytes": packed,
                             "codebook_bytes": len(json.dumps(jsonld_context(), ensure_ascii=False)),
                             "roundtrip": sorted(json.dumps(r, sort_keys=True) for r in expand_facts(facts))
                             == sorted(json.dumps(r, sort_keys=True) for r in current),
                             "missing_identifiers": kept_identifiers([r for r in current if r["status"] != "skipped"], shown)}
    result["diff"] = diff_reports(result, previous)
    return result


# ---------------------------------------------------------------- knowledge graph export (JSON-LD, stdlib only)

def jsonld_context():
    """The codebook as a JSON-LD @context: every short key expands to one public IRI (the shared ledger)."""
    context = {"@vocab": VOCAB, "@base": VOCAB.rstrip("#") + "/node/", "prov": "http://www.w3.org/ns/prov#",
               "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
               "xsd": "http://www.w3.org/2001/XMLSchema#", "url": {"@type": "@id"}, "relation": {"@type": "@vocab"},
               "assertedBy": {"@type": "@id"}, "source": {"@type": "@id"}, "target": {"@type": "@id"},
               "observedAt": {"@id": "prov:generatedAtTime", "@type": "xsd:dateTime"}, "label": "rdfs:label",
               "domain": {"@id": "rdfs:domain", "@type": "@vocab"}, "range": {"@id": "rdfs:range", "@type": "@vocab"},
               "subPropertyOf": {"@id": "rdfs:subPropertyOf", "@type": "@id"},
               "repository": {"@type": "@id"}, "previousNames": {"@container": "@set"}}
    for kind, (code, *_rest) in RELATIONS.items():
        context[code] = {"@id": VOCAB + kind, "@type": "@id", "@container": "@set"}
    return context


def _iri(node):
    repo, path = split(node)
    return f"{repo}/{path}"


def to_jsonld(result):
    """Nodes with their classes and identities, one statement per edge with its provenance, and the ontology itself."""
    ontology = [{"@id": VOCAB + name, "@type": "rdfs:Class", "label": text} for name, (_icon, text) in CLASSES.items()]
    ontology.append({"@id": VOCAB + "Evidence", "@type": "rdfs:Class",
                     "label": "one relation read from a record, with its basis (observed | derived | hypothesis), method, "
                              "comparison (match | different | unknown) and state; observedAt is the record's own time"})
    ontology.append({"@id": VOCAB + "Fact", "@type": "rdfs:Class",
                     "label": "a remaining action: one decision about one subject, numbered FLEET-n, identified by its key"})
    for kind, (code, _icon, domain, range_, group, phase, parent, label) in RELATIONS.items():
        term = {"@id": VOCAB + kind, "@type": "rdf:Property", "label": label, "code": code, "group": group,
                "domain": domain.split("|"), "range": range_.split("|")}
        if parent:
            term["subPropertyOf"] = parent
        if phase:
            term["planPhase"] = phase
        ontology.append(term)
    repos = {r["repository"]: r for r in result["targets"]["repositories"]}
    nodes = {}
    edges = result["graph"].get("edges", [])
    relations = (result["graph"].get("evidence") or {}).get("relations", [])
    for e in edges + relations:
        for end in (e["source"], e["target"]):
            nodes.setdefault(end, {"@id": _iri(end), "@type": node_class(end), "repository": split(end)[0]})
        if e["kind"] != "implied":
            nodes[e["target"]].setdefault(RELATIONS[e["kind"]][0], []).append(_iri(e["source"]))
    for name, r in repos.items():
        node = nodes.setdefault(f"{name}:", {"@id": _iri(f"{name}:"), "@type": "Repository", "repository": name})
        node.update(category=r["category"], url=f"https://github.com/{OWNER}/{name}",
                    **({"repositoryId": r["repository_id"]} if r.get("repository_id") else {}),
                    **({"previousNames": r["previous_names"]} if r.get("previous_names") else {}),
                    **({"revision": r["revision"]} if r.get("revision") not in (None, WORKTREE) else {}))
    facts = [{"@id": f"fact/{x['no']}", "@type": "Fact", "no": x["no"], "key": x["key"], "severity": x["severity"],
              "what": x["what"], "detail": x["detail"], "repository": [f"{n}/" for n in x.get("repositories", [])],
              **({"rows": x["rows"]} if x.get("rows") else {})} for x in result.get("remaining", []) if x.get("no")]
    statements = [{"@id": f"edge/{i}", "@type": "Dependency", "relation": RELATIONS[e["kind"]][0], "source": _iri(e["source"]),
                   "target": _iri(e["target"]), "assertedBy": _iri(e["lock"]), "observedAt": result.get("generated_at"),
                   **({"pin": e["pin"]} if e.get("pin") else {}), **({"blob": e["blob"]} if e.get("blob") else {})}
                  for i, e in enumerate(edges, 1)]
    statements += [{"@id": f"relation/{i}", "@type": "Evidence", "relation": RELATIONS[e["kind"]][0], "source": _iri(e["source"]),
                    "target": _iri(e["target"]), "assertedBy": _iri(e["lock"]), "checkedAt": result.get("generated_at"),
                    **({"observedAt": e["observed_at"]} if e.get("observed_at") else {}),
                    **{k: e[k] for k in ("basis", "method", "compare", "state", "pin", "sha256", "line") if e.get(k) is not None},
                    **({"value": json.dumps(e["value"], ensure_ascii=False, sort_keys=True)} if e.get("value") is not None else {})}
                   for i, e in enumerate(relations, 1)]
    return {"@context": jsonld_context(), "@id": "fleet", "generatedAt": result.get("generated_at"),
            "parentRevision": result.get("parent_revision"),
            "@graph": ontology + [nodes[k] for k in sorted(nodes)] + statements + facts}


# ---------------------------------------------------------------- compressed facts (TalkCompress ideas, no code import)
#
# Many rows say the same thing about one source ("xprobe.py 7595fe5→9d9071f" in eight consumers). A fact keeps the
# fields its rows share once and each row only with what differs: lossless (expand_facts gives the rows back),
# identifiers kept (kept_identifiers), and the cost counted with the codebook, like a shared ledger.

SHA_IN_REASON = re.compile(r"\b(at|main|since|recorded|moved|HEAD) [0-9a-f]{7,40}(→[0-9a-f]{7,40})?")
PATH_CODES = {".github/workflows/": "⚙️"}  # fixed path codes of the codebook; kept_identifiers expands them back
DERIVED = {"id": lambda r: f"{r['kind']}:{r['target']}<-{r['source']}", "repository": lambda r: split(r["target"])[0]}


def fact_subject(row):
    """What a fact is about: the record for record relations (child-heads, catalog), the source repository for
    a failure or an unreadable source (one decision for all its paths), otherwise the source file."""
    relation = RELATIONS.get(row.get("kind"))
    if relation and relation[4] == "record" and row.get("target"):
        return row["target"]
    if row.get("source") and row.get("status") in ("failure", "unavailable", "blocked") and relation:
        return split(row["source"])[0] + ":"
    return row.get("source") or row.get("repository", "")


def fact_pattern(row):
    """The reason without the parts each member has on its own: moving SHAs and the old blob of a copy."""
    if RELATIONS.get(row.get("kind"), ("",) * 5)[4] == "record":
        return "behind main" if row.get("status") == "updated" else str(row.get("reason", ""))
    pattern = SHA_IN_REASON.sub(lambda m: m.group(1), str(row.get("reason", ""))).strip()
    return re.sub(r"^blob [0-9a-f]+→", "blob →", pattern)


def fact_key(row):
    """Semantic identifier of what a row asserts: status, relation, subject and the change."""
    return "|".join((row.get("status", ""), row.get("kind", ""), fact_subject(row), fact_pattern(row)))


def _derivable(row, field):
    try:
        return field in DERIVED and row[field] == DERIVED[field](row)
    except (KeyError, TypeError):
        return False


def compress_rows(rows):
    """[{key, common, members}]: rows with the same fact share fields once; members keep only what differs.

    Fields the codebook derives (the row id, the repository of the target) are dropped and rebuilt by
    expand_facts, so the round trip is exact.
    """
    groups = {}
    for row in rows:
        groups.setdefault(fact_key(row), []).append({f: v for f, v in row.items() if not _derivable(row, f)}
                                                    | {"_derived": sorted(f for f in DERIVED if _derivable(row, f))})
    facts = []
    for key, members in groups.items():
        first = members[0]
        common = {f: v for f, v in first.items() if all(f in m and m[f] == v for m in members[1:])}
        facts.append({"key": key, "common": common, "members": [{f: v for f, v in m.items() if f not in common} for m in members]})
    return facts


def expand_facts(facts):
    rows = []
    for fact in facts:
        for member in fact["members"]:
            row = {**fact["common"], **member}
            for field in row.pop("_derived", []):
                row[field] = DERIVED[field](row)
            rows.append(row)
    return rows


def encode_path(path):
    for long, code in PATH_CODES.items():
        if path.startswith(long):
            return code + path[len(long):]
    return path


def kept_identifiers(rows, text):
    """Identifiers of rows (repository, path, source) a compressed rendering dropped, after expanding path codes."""
    for long, code in PATH_CODES.items():
        text = re.sub(re.escape(code) + r"(?=[\w.-])", long, text)
    missing = set()
    for row in rows:
        for value in (row.get("repository"), split(row.get("target") or "")[1], *split(row.get("source") or "")):
            if value and value not in text:
                missing.add(value)
    return sorted(missing)


def numbered(items, previous):
    """FLEET-n numbers that stay with the same key across reports; new keys get the next free numbers."""
    old = {x["key"]: x["no"] for x in (previous or {}).get("remaining", []) if x.get("key") and x.get("no")}
    used = {int(n.split("-")[1]) for n in old.values() if re.fullmatch(r"FLEET-\d+", n)}
    for item in items:
        if item["key"] in old:
            item["no"] = old[item["key"]]
    for item in items:
        if "no" not in item:
            number = max(used, default=0) + 1
            used.add(number)
            item["no"] = f"FLEET-{number}"
    return items


def remaining(result, previous=None):
    """What still needs a person or a follow-up run, one item per fact, most urgent first, with stable numbers."""
    items = [{"severity": "failure", "key": "cycle:" + "→".join(c), "what": "execution cycle", "detail": " → ".join(c),
              "repositories": sorted({split(n)[0] for n in c})} for c in result["graph"]["cycles"]]
    for fact in result.get("facts") or compress_rows(result["rows"]):
        common = fact["common"]
        if common.get("status") not in ("failure", "blocked", "unavailable", "updated"):
            continue
        rows = expand_facts([fact])
        subject = fact_subject(rows[0]) if common.get("kind") in RELATIONS else rows[0]["id"]
        record = RELATIONS.get(common.get("kind"), ("",) * 5)[4] == "record"
        same = len({r["reason"] for r in rows}) == 1
        where = {}
        for r in rows:  # repository (paths) — or, for a record, the repositories and sources it lists
            if record:
                where.setdefault(split(r["source"])[0], []).extend([split(r["source"])[1]] if split(r["source"])[1] else [])
                continue
            path = encode_path(split(r.get("target") or "")[1] or r["id"])
            if split(subject)[1] == "" and r.get("source"):  # a repository fact: name the source path of each copy
                path += f" ← {split(r['source'])[1]}"
            elif not same and (old := re.match(r"blob ([0-9a-f]+)→", r["reason"])):
                path += f" ←{old.group(1)}"  # version skew: which old blob each copy holds
            where.setdefault(r["repository"], []).append(path)
        places = ", ".join(f"{repo} ({', '.join(sorted(set(paths)))})" if paths else repo for repo, paths in sorted(where.items()))
        reason = rows[0]["reason"] if same else fact_pattern(rows[0])
        items.append({"severity": common["status"], "key": fact["key"],
                      "what": f"{ICON_OF.get(common.get('kind'), '')} {subject}".strip(),
                      "detail": f"{reason} → {len(rows)}× {places}", "repositories": sorted(where), "rows": len(rows)})
    for t in result["targets"]["repositories"]:
        if t["category"] == "insufficient":
            items.append({"severity": "insufficient", "key": f"target:{t['repository']}", "what": t["repository"],
                          "detail": "; ".join(t["reasons"]), "repositories": [t["repository"]]})
    evidence = result["graph"].get("evidence") or {}
    for f in result["graph"]["findings"] + evidence.get("findings", []):
        items.append({"severity": "insufficient", "key": f"{f['kind']}:{f['repository']}:{f.get('subject', '')}",
                      "what": f"{f['repository']} {f['kind']}", "detail": f["detail"], "repositories": [f["repository"]]})
    for cycle in evidence.get("cycles", []):
        items.append({"severity": "insufficient", "key": "relation-cycle:" + "→".join(cycle), "what": "relation cycle (reported, not blocking)",
                      "detail": " → ".join(cycle), "repositories": sorted({split(n)[0] for n in cycle})})
    for repo, cover in sorted(evidence.get("coverage", {}).items()):
        retest = cover.get("retest") or {}
        if retest.get("unregistered_count"):
            shown = ", ".join(retest["unregistered"][:6]) + (" …" if retest["unregistered_count"] > 6 else "")
            items.append({"severity": "insufficient", "key": f"retest-unregistered:{repo}", "what": f"🧪 {repo} retest not registered",
                          "detail": f"{retest['unregistered_count']} of {retest['subjects']} file(s) with upstream relations have no "
                                    f"test, workflow or locked_ci link: {shown}", "repositories": [repo]})
    order = {"failure": 0, "blocked": 1, "unavailable": 2, "updated": 3, "insufficient": 4}
    return numbered(sorted(items, key=lambda x: (order[x["severity"]], x["key"])), previous)


def diff_reports(current, previous):
    """Rows whose status changed, appeared or disappeared since the previous report; same for target categories."""
    if not previous or previous.get("schema") != REPORT_SCHEMA:
        return {"previous_at": None, "changed": [], "new": [], "gone": [], "targets": []}
    before = {r["id"]: r for r in previous.get("rows", [])}
    after = {r["id"]: r for r in current["rows"]}
    old_targets = {t["repository"]: t["category"] for t in previous.get("targets", {}).get("repositories", [])}
    return {"previous_at": previous.get("generated_at"),
            "changed": [{"id": k, "from": before[k]["status"], "to": after[k]["status"]} for k in sorted(after)
                        if k in before and before[k]["status"] != after[k]["status"]],
            "new": sorted(k for k in after if k not in before), "gone": sorted(k for k in before if k not in after),
            "targets": [{"repository": t["repository"], "from": old_targets.get(t["repository"]), "to": t["category"]}
                        for t in current["targets"]["repositories"] if old_targets.get(t["repository"]) != t["category"]]}


def _brief(rev):
    return "worktree" if rev == WORKTREE else (rev or "—")[:8]


SECTIONS = {"targets": ("targets",), "graph": ("graph",), "impact": ("impact",), "plan": ("plan", "rows"),
            "test": ("rows",), "report": ("targets", "graph", "plan", "rows", "remaining")}


def render(result, fmt="text", *, sections=SECTIONS["report"]):
    """Iconic text or Markdown from any command result; JSON is the result itself."""
    if fmt == "json":
        return json.dumps(result, ensure_ascii=False, indent=1)
    md = fmt == "markdown"
    lines = []

    def head(text):
        lines.append(("### " if md else "") + text)

    def table(header, rows):
        rows = list(rows)
        if not rows:
            return
        if md:
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "---|" * len(header))
            lines.extend("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows)
        else:
            lines.extend("  " + "  ".join(str(c) for c in row) for row in rows)
    if "targets" in sections and "targets" in result:
        t = result["targets"]
        head(f"🎯 {t['counts']['target']}  🚫 {t['counts']['excluded']}  ❔ {t['counts']['insufficient']}")
        shown = t["repositories"] if sections == SECTIONS["targets"] else [r for r in t["repositories"] if r["category"] != "target"]
        table(("", "repository", "roles", "rev", "reasons"),
              [(ICON[r["category"]], r["repository"], ",".join(r["roles"]), _brief(r["revision"]), "; ".join(r["reasons"])) for r in shown])
    if "graph" in sections and "graph" in result:
        g = result["graph"]
        code = [m for m in g["mutual"] if m["kind"] == "code"]
        head(f"🔗 {len(g['edges'])} edges / {g['nodes']} nodes · " + " · ".join(f"{k} {v}" for k, v in g["kinds"].items())
             + f"  🔁 mutual code {len(code)} · record {len(g['mutual']) - len(code)}  ⭕ cycles {len(g['cycles'])}")
        table(("", "repository", "repository", "a→b", "b→a"),
              [("🔁" if m["kind"] == "code" else "🗂️", *m["repositories"], ",".join(m["a_to_b"]), ",".join(m["b_to_a"]))
               for m in g["mutual"] if m["kind"] == "code" or sections == SECTIONS["graph"]])
        table(("", "execution cycle"), [("⭕", " → ".join(c)) for c in g["cycles"]])
        table(("", "repository", "finding", "detail"), [("❔", f["repository"], f["kind"], f["detail"]) for f in g["findings"]])
    if "impact" in sections and "impact" in result:
        i = result["impact"]
        direct = sorted(k for k, v in i["repositories"].items() if v["direct"])
        indirect = sorted(k for k, v in i["repositories"].items() if v["indirect"] and not v["direct"])
        head(f"💥 {', '.join(i['changed'])} → 🎯 direct {len(direct)} · ↪️ indirect only {len(indirect)}")
        table(("depth", "node", "kind", "via"), [(r["depth"], r["node"], r["kind"], " → ".join(r["via"][1:-1]) or "direct")
                                                 for r in i["rows"] if r["kind"] != "implied"])
        if i["unknown"]:
            lines.append("❔ no record names this as a source (tests below, if any, were found by reading the tree): " + ", ".join(i["unknown"]))
        view = i.get("view")
        if view:
            groups = _sibling("fleet_records").GROUP_JA
            head(f"変更の影響先 {len(view['affected'])}")
            table(("repository", "", "relations"), [(a["repository"], "直接" if a["direct"] else "間接",
                                                    ", ".join(groups.get(g, g) for g in a["groups"])) for a in view["affected"]])
            head(f"判断の根拠 {len(view['basis'])}" + (f" (+{view['truncated']['basis']})" if view["truncated"]["basis"] else ""))
            table(("node", "relation", "record", "method / basis", "pin · compare · state"),
                  [(b["node"], f"{ICON_OF.get(b['kind'], '')} {b['kind']}", b.get("lock", "—"),
                    f"{b.get('method', 'lock-file')} / {b.get('basis', 'observed')}" + ("" if b.get("registered") is None else
                                                                                       " · 登録済み" if b["registered"] else " · 未登録"),
                    " · ".join(x for x in ((b.get("pin") or "")[:8], (b.get("blob") or "")[:8], b.get("compare", ""), b.get("state", "")) if x) or "—")
                   for b in view["basis"] if b["kind"] != "tested_by"])
            head(f"再テストが必要な対象 {len(view['retest'])}")
            table(("test", "for", "method"), [(r["test"], r["for"], f"{r['method']} / {r['basis']}") for r in view["retest"]])
            if view["recheck"]:
                head(f"再検証が必要な記録 {len(view['recheck'])}")
                table(("record", "relation", "from", "compare · state"),
                      [(r["record"], r["kind"], r["via"], " · ".join(x for x in (r.get("compare"), r.get("state")) if x) or "—")
                       for r in view["recheck"]])
            if view["unregistered"]:
                head(f"再テスト対応が未登録 {len(view['unregistered'])}")
                table(("node",), [(n,) for n in view["unregistered"]])
            if view.get("candidates"):
                head(f"候補（仮説・未確認、再検証は要求しない） {len(view['candidates'])}")
                table(("node", "relation", "from", "record"), [(c["node"], c["kind"], c["from"], c["record"]) for c in view["candidates"]])
    if "plan" in sections and "plan" in result:
        p = result["plan"]
        counts = p.get("counts", {})
        head("🧮 " + "  ".join(f"{ICON[s]} {counts.get(s, 0)}" for s in STATUSES)
             + f"  🌊 {len(p['waves'])} waves / {len(p['steps'])} steps" + ("  (dry-run)" if p.get("dry_run") else ""))
        table(("🌊", "step", "", "why", "command"),
              [(s.get("wave", ""), s["id"], "🤖" if s["auto"] else "👤", "; ".join(s["reasons"])[:160],
                "$ " + " ".join(s.get("argv") or ["(manual)"])) for s in p["steps"]])
    rows = result.get("rows") or []
    shown = [r for r in rows if r["status"] != "skipped" or r.get("kind") in ("check", "test", "step")]
    if "rows" in sections and shown:
        facts = compress_rows(shown)
        head(f"📋 {len(shown)} of {len(rows)} rows as {len(facts)} facts" + (" (rows that need nothing are hidden)" if len(shown) < len(rows) else ""))
        table(("", "", "fact", "×", "rev", "checked", "where"),
              [(ICON[f["common"].get("status", "skipped")], ICON_OF.get(f["common"].get("kind"), ""),
                f["common"].get("source") or f["common"].get("id") or f["common"].get("repository", ""), len(f["members"]),
                _brief(f["common"].get("revision")), (f["common"].get("checked_at") or "")[11:19],
                (str(f["common"].get("reason") or "") + " → " if "reason" in f["common"] else "")
                + ", ".join(r["id"] if "reason" in f["common"] else f"{r['id']}: {r['reason']}" for r in expand_facts([f]))[:240])
               for f in facts])
    if result.get("stale"):
        lines.append(f"🕰️ {len(result['stale'])} stale row(s) kept out of the current view (their repository moved)")
    diff = result.get("diff") or {}
    if diff.get("previous_at"):
        head(f"Δ since {diff['previous_at']}: changed {len(diff['changed'])} · new {len(diff['new'])} · gone {len(diff['gone'])}")
        table(("Δ", "id", "from", "to"), [("Δ", c["id"], c["from"], c["to"]) for c in diff["changed"]]
              + [("Δ", c["repository"], c["from"], c["to"]) for c in diff["targets"]])
    if "remaining" in sections and result.get("remaining"):
        head(f"📌 remaining {len(result['remaining'])}")
        table(("", "no", "what", "detail"), [(ICON.get(x["severity"], "❔"), x.get("no", ""), x["what"], x["detail"])
                                             for x in result["remaining"]])
    if "compression" in result:
        c = result["compression"]
        lines.append(f"🗜️ {c['rows']} rows → {c['facts']} facts · {c['raw_bytes']:,} B → {c['fact_bytes']:,} B"
                     f" (+ codebook {c['codebook_bytes']:,} B) · roundtrip {'✅' if c['roundtrip'] else '❌'}"
                     f" · identifiers {'✅' if not c['missing_identifiers'] else '❌ ' + ', '.join(c['missing_identifiers'][:5])}")
    if "cache" in result:
        c = result["cache"]
        lines.append(f"♻️ git {c['git_calls']} · clones {c['clones']} · fetches {c['fetches']} · cache hits {c['cache_hits']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- query the published graph (no checkouts)

GRAPH_URL = "https://myon-bioinformatics.github.io/myon-bioinformatics/fleet.jsonld"
QUERY_KINDS = ("deps", "who", "facts", "why", "reverify")


def load_graph(source=GRAPH_URL, *, timeout=30):
    """A fleet JSON-LD document from a file path or an https URL (anonymous GET, no token)."""
    text = str(source)
    if text.startswith("https://"):
        import urllib.request
        request = urllib.request.Request(text, headers={"Accept": "application/ld+json, application/json",
                                                        "User-Agent": "vendor-fleet-plan"})
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
            document = json.loads(response.read().decode("utf-8"))
    else:
        document = json.loads(Path(text).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("@graph"), list):
        raise ValueError("not a fleet JSON-LD document")
    return document


def _node_of(iri):
    repo, _, path = str(iri).partition("/")
    return f"{repo}:{path}"


def _edges_of(document):
    """(dependency edges, evidence relations) back as edge dicts from the JSON-LD statements."""
    by_code = {r[0]: kind for kind, r in RELATIONS.items()}
    deps, relations = [], []
    for g in document["@graph"]:
        if g.get("@type") not in ("Dependency", "Evidence"):
            continue
        edge = {"kind": by_code.get(g.get("relation"), g.get("relation")), "source": _node_of(g["source"]),
                "target": _node_of(g["target"]), "lock": _node_of(g.get("assertedBy", "")),
                **{k: g[k] for k in ("basis", "method", "compare", "state", "pin", "sha256", "line") if k in g},
                **({"observed_at": g["observedAt"]} if g.get("@type") == "Evidence" and g.get("observedAt") else {})}
        (deps if g["@type"] == "Dependency" else relations).append(edge)
    return deps, relations


def _resolve_node(term, edges):
    """An exact 'Repo:path' (or IRI) when the graph has it, else the nodes whose name contains term."""
    text = str(term).strip()
    node = parse_node(text) if ":" in text else _node_of(text) if "/" in text else f"{short(text)}:"
    nodes = {n for e in edges for n in (e["source"], e["target"])}
    if node in nodes or (node.endswith((":", "/")) and any(n.startswith(node) for n in nodes)):
        return [node]
    entries = sorted(n for n in nodes if n.startswith(node + "@"))  # 'ledger#P1' → its judgements 'ledger#P1@time'
    return entries[:20] or sorted(n for n in nodes if text.casefold() in n.casefold())[:20]


def query_why(document, term):
    """判断の根拠: inbound relations of a node, breadth-first, each with the record that asserts it."""
    deps, relations = _edges_of(document)
    rows = []
    for node in _resolve_node(term, deps + relations):
        for r in why(deps + relations, node)["rows"]:
            rows.append({"node": node, "depth": r["depth"], "relation": r["kind"], "source": r["source"], "target": r["target"],
                         "asserted_by": r["lock"], **{k: r[k] for k in ("basis", "method", "compare", "state", "pin", "observed_at") if k in r}})
    return rows


def query_reverify(document, term):
    """変更の影響先 and 再テスト / 再検証 targets of a change, from the published graph alone (no checkouts)."""
    deps, relations = _edges_of(document)
    prefixes = {}
    for e in deps:
        if e["kind"] == "subtree":
            hub, prefix = split(e["source"])
            prefixes.setdefault(hub, []).append(prefix.rstrip("/"))
    graph = {"edges": deps, "prefixes": prefixes, "evidence": {"relations": relations}}
    rows = []
    for node in _resolve_node(term, deps + relations)[:5]:
        view = impact(graph, [node])["view"]
        rows += [{"changed": node, "answer": "affected", **a} for a in view["affected"]]
        rows += [{"changed": node, "answer": "retest", **r} for r in view["retest"]]
        rows += [{"changed": node, "answer": "recheck", **r} for r in view["recheck"]]
        rows += [{"changed": node, "answer": "unregistered", "node": n} for n in view["unregistered"]]
    return rows


def _graph_parts(document):
    repos = {g["@id"]: g for g in document["@graph"] if g.get("@type") == "Repository"}
    statements = [g for g in document["@graph"] if g.get("@type") == "Dependency"]
    facts = [g for g in document["@graph"] if g.get("@type") == "Fact"]
    by_code = {r[0]: kind for kind, r in RELATIONS.items()}
    return repos, statements, facts, by_code


def query_who(document, term):
    """Repositories matching a name, a previous name or a repository_id; names are labels, the id is the identity."""
    repos, statements, _facts, by_code = _graph_parts(document)
    needle = str(term).strip().rstrip("/").casefold()
    needle = short(needle) if "/" in needle else needle
    rows = []
    for node_id, node in sorted(repos.items()):
        name = node_id.rstrip("/")
        previous = [p.casefold() for p in node.get("previousNames", [])]
        if not (name.casefold() == needle or str(node.get("repositoryId", "")) == needle or needle in previous):
            continue
        incoming = sum(s["target"].startswith(node_id) for s in statements)
        outgoing = sum(s["source"].startswith(node_id) for s in statements)
        rows.append({"repository": name, "repository_id": node.get("repositoryId"), "matched_by": "name" if name.casefold() == needle
                     else "repository_id" if str(node.get("repositoryId", "")) == needle else "previous name",
                     "previous_names": node.get("previousNames", []), "category": node.get("category"),
                     "revision": node.get("revision"), "url": node.get("url"), "depends_on": incoming, "used_by": outgoing})
    return rows


def query_deps(document, repo):
    """What one repository depends on: every statement whose target is inside it, grouped by relation."""
    repos, statements, _facts, by_code = _graph_parts(document)
    name = short(str(repo).strip().rstrip("/"))
    matches = query_who(document, name)
    if matches:
        name = matches[0]["repository"]  # a previous name resolves to the current one
    rows = []
    for s in statements:
        kind = by_code.get(s.get("relation"), s.get("relation"))
        if s["target"].startswith(name + "/") and kind != "implied":
            rows.append({"relation": kind, "icon": ICON_OF.get(kind, ""), "source": s["source"], "target": s["target"],
                         "pin": s.get("pin"), "asserted_by": s.get("assertedBy")})
        elif s["source"] == name + "/" and kind in ("heads", "subtree"):
            rows.append({"relation": kind, "icon": ICON_OF.get(kind, ""), "source": s["source"], "target": s["target"],
                         "pin": s.get("pin"), "asserted_by": s.get("assertedBy"), "note": "hub record about this repository"})
    rows.sort(key=lambda r: (r["relation"], r["source"].casefold(), r["target"]))
    return rows


def query_facts(document, term="", *, severity=None):
    """Remaining actions whose number, key, subject or detail contains term."""
    _repos, _statements, facts, _by_code = _graph_parts(document)
    needle = str(term or "").strip().casefold()
    rows = []
    for f in facts:
        text = " ".join(str(f.get(k, "")) for k in ("no", "key", "what", "detail")).casefold()
        if (not needle or needle in text) and (severity is None or f.get("severity") == severity):
            rows.append({"no": f["no"], "severity": f["severity"], "what": f["what"], "key": f["key"], "detail": f["detail"],
                         "repositories": [r.rstrip("/") for r in f.get("repository", [])]})
    rows.sort(key=lambda r: int(r["no"].split("-")[1]) if r["no"].startswith("FLEET-") else 0)
    return rows


def query(kind, term="", *, graph=GRAPH_URL, severity=None, document=None):
    """One call for Python, Node (py_launch.cjs … query … --format json) and MCP: {kind, term, graph, generated_at, rows}."""
    if kind not in QUERY_KINDS:
        raise ValueError("kind must be one of " + ", ".join(QUERY_KINDS))
    document = document or load_graph(graph)
    if kind == "deps":
        rows = query_deps(document, term)
    elif kind == "who":
        rows = query_who(document, term)
    elif kind == "why":
        rows = query_why(document, term)
    elif kind == "reverify":
        rows = query_reverify(document, term)
    else:
        rows = query_facts(document, term, severity=severity)
    return {"schema": "vendor-fleet-query/1", "kind": kind, "term": term, "graph": str(graph),
            "generated_at": document.get("generatedAt"), "parent_revision": document.get("parentRevision"), "rows": rows}


def render_query(result, fmt="text"):
    if fmt == "json":
        return json.dumps(result, ensure_ascii=False, indent=1)
    md = fmt == "markdown"
    kind, rows = result["kind"], result["rows"]
    lines = [("### " if md else "") + f"🔎 {kind} {result['term']} · {len(rows)} row(s) · graph {result.get('generated_at') or '?'}"]

    def table(header, body):
        if md:
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "---|" * len(header))
            lines.extend("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in body)
        else:
            lines.extend("  " + "  ".join(str(c) for c in row) for row in body)
    if kind == "who":
        table(("repository", "repository_id", "matched by", "previous names", "category", "rev", "depends on", "used by"),
              [(r["repository"], r["repository_id"] or "—", r["matched_by"], ", ".join(r["previous_names"]) or "—", r["category"],
                (r["revision"] or "—")[:8], r["depends_on"], r["used_by"]) for r in rows])
    elif kind == "deps":
        table(("", "relation", "source", "target", "pin", "asserted by"),
              [(r["icon"], r["relation"], r["source"], r["target"], (r["pin"] or "—")[:8], r["asserted_by"]) for r in rows])
    elif kind == "why":
        table(("判断の根拠", "depth", "relation", "source", "basis", "compare", "state", "asserted by"),
              [(r["node"], r["depth"], f"{ICON_OF.get(r['relation'], '')} {r['relation']}", r["source"], r.get("basis", "observed"),
                r.get("compare", "—"), r.get("state", "—"), r["asserted_by"]) for r in rows])
    elif kind == "reverify":
        labels = {"affected": "変更の影響先", "retest": "再テストが必要な対象", "recheck": "再検証が必要な記録", "unregistered": "再テスト対応が未登録"}
        table(("", "answer", "node", "detail"),
              [(r["changed"], labels[r["answer"]], r.get("repository") or r.get("test") or r.get("record") or r.get("node"),
                ", ".join(r.get("groups", [])) if r["answer"] == "affected" else
                f"for {r['for']} ({r['method']})" if r["answer"] == "retest" else
                f"{r['kind']} ← {r['via']} ({r.get('compare') or '—'})" if r["answer"] == "recheck" else "")
               for r in rows])
    else:
        table(("", "no", "what", "detail"), [(ICON.get(r["severity"], "❔"), r["no"], r["what"], r["detail"]) for r in rows])
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

COMMANDS = ("targets", "graph", "impact", "plan", "test", "report", "query")


def _registration_main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only vendor fleet registration plan "
                                     f"(fleet commands: {', '.join(COMMANDS)}; see --help of each).")
    parser.add_argument("--catalog", default="vendor-catalog.json")
    parser.add_argument("--registry", default="vendor-consumers.json")
    parser.add_argument("--consumer", action="append", default=[])
    parser.add_argument("--fail-on-missing", action="store_true",
                        help="exit 1 for unregistered non-skipped source candidates")
    args = parser.parse_args(argv)
    try:
        cat = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        reg = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        result = plan(cat, reg, active_consumers=args.consumer or None)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if args.fail_on_missing and result["counts"]["needs_registration"] else 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"vendor fleet plan: {exc}", file=sys.stderr)
        return 2


def _fleet_parser():
    parser = argparse.ArgumentParser(prog="vendor_fleet_plan.py", description=__doc__.split("\n\n")[0],
                                     allow_abbrev=False,
                                     epilog="Exit codes: 0 ok; 1 a finding with --check, or a failing/blocked test or applied step; 2 input error.")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--root", default=str(ROOT), help="parent checkout (default: this file's repository)")
    common.add_argument("--checkouts", metavar="DIR", help="directory of clones named like the repositories")
    common.add_argument("--fetch", action="store_true", help="clone missing public repositories once (anonymous git, blob-less)")
    common.add_argument("--at", default="main",
                        help="'main' (default), 'heads' (each child at its child-heads.json revision) or one full SHA")
    common.add_argument("--repos", default="", help="comma-separated repositories to analyse (the parent is always included)")
    common.add_argument("--format", choices=("text", "json", "markdown", "jsonld"), default="text",
                        help="jsonld: the graph as JSON-LD (codebook @context, nodes, provenance per edge)")
    common.add_argument("--out", metavar="FILE", help="also write the JSON result to FILE")
    common.add_argument("--check", action="store_true", help="exit 1 while a finding remains")
    common.add_argument("--audit", metavar="FILE", help="parent-audit/1 snapshot (ci_status): PR dependencies and CI per head")
    common.add_argument("--no-evidence", action="store_true",
                        help="skip the evidence relations (tests, generators, receipts, corpus, ledgers, provenance)")

    def jobs(p, default):
        p.add_argument("--jobs", type=int, default=default, help=f"parallel jobs (default {default})")
        p.add_argument("--timeout", type=float, default=900, help="seconds per command (default 900)")
    sub.add_parser("targets", parents=[common], help="🎯 detected / 🚫 excluded / ❔ insufficient repositories")
    sub.add_parser("graph", parents=[common], help="🔗 dependency edges, 🔁 mutual repositories, ⭕ execution cycles")
    p = sub.add_parser("impact", parents=[common], help="💥 direct and indirect consumers of changed files")
    p.add_argument("changed", nargs="+", metavar="REPO:PATH", help="'Repo:path', 'Repo:' or a parent path")
    p = sub.add_parser("plan", parents=[common], help="🌊 ordered update plan (dry-run unless --apply)")
    p.add_argument("--changed", action="append", default=[], metavar="REPO:PATH", help="limit the plan to these sources")
    p.add_argument("--apply", action="store_true",
                   help="run the automatic steps in local checkouts: vendor_sync update --only, then verify")
    jobs(p, 2)
    for name in ("test", "report"):
        p = sub.add_parser(name, parents=[common], help="🧪 discover and run tests across repositories" if name == "test"
                           else "📋 targets + graph + plan (+ tests) with diff and remaining actions")
        p.add_argument("-p", "--pattern", default="test*.py", help="test file pattern, like unittest -p (default test*.py)")
        p.add_argument("-k", dest="keywords", action="append", default=[], help="test name pattern, like unittest -k")
        p.add_argument("--related", action="append", default=[], metavar="TEXT", help="keep test files that mention TEXT")
        p.add_argument("--runner", choices=("all", "unittest", "pytest"), default="all")
        p.add_argument("--list", action="store_true", help="load and list tests without running them")
        p.add_argument("--no-checks", action="store_true", help="skip the offline vendor_sync check prerequisite")
        jobs(p, 4)
        if name == "report":
            p.add_argument("--with-tests", action="store_true", help="also run the selected tests")
            p.add_argument("--previous", metavar="FILE", help="previous report JSON (diff, carried test rows)")
            p.add_argument("--changed", action="append", default=[], metavar="REPO:PATH")
    p = sub.add_parser("query", help="🔎 ask the published graph: deps REPO · who NAME|ID · facts [TEXT]; no checkouts needed",
                       allow_abbrev=False)
    p.add_argument("kind", choices=QUERY_KINDS)
    p.add_argument("term", nargs="?", default="", help="repository (deps), name / previous name / repository_id (who), "
                   "text (facts), 'Repo:path' or text (why: 判断の根拠 · reverify: 影響先・再テスト・再検証)")
    p.add_argument("--graph", default=GRAPH_URL, metavar="FILE_OR_URL", help=f"fleet JSON-LD (default: the published {GRAPH_URL})")
    p.add_argument("--severity", choices=("failure", "blocked", "unavailable", "updated", "insufficient"), help="facts only")
    p.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["unittest-worker"]:
        return unittest_worker(argv[1:])
    if argv[:1] == ["query"]:
        args = _fleet_parser().parse_args(argv)
        try:
            result = query(args.kind, args.term, graph=args.graph, severity=args.severity)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"vendor fleet: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(render_query(result, args.format))
        return 0 if result["rows"] else 1
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        return _registration_main(argv)
    if argv[0] in ("-h", "--help"):
        _fleet_parser().print_help()
        return 0
    args = _fleet_parser().parse_args(argv)
    scratch = None
    try:
        checkouts = args.checkouts
        if args.fetch and not checkouts:
            checkouts = scratch = tempfile.mkdtemp(prefix="fleet-checkouts-")
        repos = [x.strip() for x in args.repos.split(",") if x.strip()] or None
        fleet = Fleet(args.root, checkouts, fetch=args.fetch, at=args.at, repos=repos)
        records = load_records(fleet)
        snapshot = json.loads(Path(args.audit).read_text(encoding="utf-8")) if args.audit else None
        if snapshot is not None and snapshot.get("schema") != "parent-audit/1":
            raise ValueError("--audit expects a parent-audit/1 snapshot (ci_status data.json)")
        if args.command == "report":
            previous = json.loads(Path(args.previous).read_text(encoding="utf-8")) if args.previous else None
            tests = None
            if args.with_tests:
                discovery = discover(fleet, records)
                tests = execute(fleet, test_jobs(fleet, discovery, repos=repos, pattern=args.pattern, keywords=args.keywords,
                                                 related=args.related, runner=args.runner, list_only=args.list,
                                                 checks=not args.no_checks), workers=args.jobs, timeout=args.timeout)
            result = report(fleet, previous=previous, changed=args.changed or None, tests=tests, snapshot=snapshot,
                            evidence=not args.no_evidence)
            code = 1 if args.check and result["remaining"] else 0
        else:
            discovery = discover(fleet, records)
            result = {"schema": REPORT_SCHEMA, "generated_at": now(), "command": args.command, "targets": discovery}
            code = 1 if args.check and args.command == "targets" and discovery["counts"]["insufficient"] else 0
            if args.command in ("graph", "impact", "plan"):
                graph = build_graph(fleet, discovery, records, evidence=not args.no_evidence, snapshot=snapshot)
                result["graph"] = {k: v for k, v in graph.items() if k != "order"}
                if args.command == "graph" and args.check and graph["cycles"]:
                    code = 1
                if args.command == "impact":
                    revisions = {r["repository"]: r["revision"] for r in discovery["repositories"]
                                 if r["category"] != "excluded" and r["revision"]}

                    def tests(repo, paths):
                        if repo not in revisions or args.no_evidence:
                            return []
                        present = fleet.files(repo, revisions[repo])
                        return _sibling("fleet_records").tests_for(fleet, repo, revisions[repo], [x for x in paths if x in present])
                    result["impact"] = impact(graph, args.changed, tests=tests)
                if args.command == "plan":
                    planned = plan_updates(fleet, graph, discovery, changed=args.changed or None)
                    result["plan"] = {k: v for k, v in planned.items() if k != "rows"}
                    result["rows"] = planned["rows"]
                    if args.apply:
                        applied = apply_plan(fleet, planned, discovery, workers=args.jobs, timeout=args.timeout)
                        result["plan"]["dry_run"] = False
                        result["rows"] = result["rows"] + applied
                        code = 1 if any(r["status"] in ("failure", "blocked") for r in applied) else 0
                    result["counts"] = {s: sum(r["status"] == s for r in result["rows"]) for s in STATUSES}
                    if args.check and (planned["steps"] or result["counts"]["failure"] or result["counts"]["unavailable"]):
                        code = 1
            if args.command == "test":
                planned_jobs = test_jobs(fleet, discovery, repos=repos, pattern=args.pattern, keywords=args.keywords,
                                         related=args.related, runner=args.runner, list_only=args.list, checks=not args.no_checks)
                result["rows"] = execute(fleet, planned_jobs, workers=args.jobs, timeout=args.timeout)
                # Expected jobs against result rows: a job without a row is not_collected, never a success.
                result["runs"] = _sibling("fleet_records").compare_runs(list(planned_jobs), result["rows"])
                result["counts"] = {s: sum(r["status"] == s for r in result["rows"]) for s in STATUSES}
                code = 1 if result["counts"]["failure"] or result["counts"]["blocked"] or \
                    (args.check and result["counts"]["unavailable"]) else 0
            result["cache"] = dict(fleet.stats)
        if args.out:
            Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if args.format == "jsonld":
            if "graph" not in result:
                raise ValueError("--format jsonld needs graph, impact, plan or report")
            print(json.dumps(to_jsonld(result), ensure_ascii=False, indent=1))
        else:
            print(render(result, args.format, sections=SECTIONS[args.command]))
        return code
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"vendor fleet: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
