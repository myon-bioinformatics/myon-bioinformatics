#!/usr/bin/env python3
"""Read-only GitHub CI status index. Python stdlib only.

The ``--audit`` mode takes the same observations across every open PR and Issue of the
organization (via the vendored gh_identity) and writes ``data.json`` plus a read-only
``index.html`` built from the vendored web_ui.py helpers: the parent audit dashboard.

Exit codes: 0 = observation succeeded (with --ci-check, all states are COMPLETE or
NO CHECKS); 1 = --ci-check observed a non-passing state; 2 = observation or usage error.
Observation errors take precedence over --ci-check failures. --json uses the same codes.
Both output formats include the UTC observation timestamp. Exception text is omitted
unless ``--explain`` is set; that flag adds detail (token values removed) and hint.
"""
import argparse
import base64
import datetime
import json
import os
import re
import urllib.error
import urllib.request
from html import escape
from urllib.parse import quote
import importlib.util
from pathlib import Path


def _load_ghi():
    source = Path(__file__).resolve().parent / "vendor" / "gh_identity.py"
    if not source.is_file():
        raise RuntimeError("canonical gh_identity.py is not present")
    spec = importlib.util.spec_from_file_location("ci_status_ghi", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    ghi = _load_ghi()
except RuntimeError:
    ghi = None

OWNER = "myon-bioinformatics"
__version__ = "0.2.0"
__all__ = ["repository", "observe", "summarize_checks", "render", "audit", "audit_repository", "link_dependencies",
           "workflow_facts", "check_conventions", "conventions", "render_conventions",
           "load_records", "diff_snapshots", "render_audit_html", "write_audit_site", "main"]

def get(path):
    """Compatibility shim backed by GHI transport; no independent HTTP client."""
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    return ghi.request("GET", path.lstrip("/"))

def repository(name):
    if not name or "/" in name and name.count("/") != 1:
        raise ValueError("invalid repository")
    parts = (name if "/" in name else OWNER + "/" + name).split("/")
    if not all(p not in ("", ".", "..") and all(c.isascii() and (c.isalnum() or c in "-_.") for c in p) for p in parts):
        raise ValueError("invalid repository")
    return "/".join(parts)

def observe(name, pr=None):
    name = repository(name)
    base = "/repos/" + name
    if pr is not None:
        pull = get(base + "/pulls/" + str(pr))
        sha = pull["head"]["sha"]
        target = "PR #" + str(pr)
    else:
        meta = get(base)
        ref = meta["default_branch"]
        sha = get(base + "/git/ref/heads/" + quote(ref, safe=""))["object"]["sha"]
        target = ref
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    observation = ghi.checks_for_sha(name, sha)
    if not observation["complete"]:
        raise RuntimeError("check pagination incomplete")
    checks = observation["checks"]
    rows, state, pending = summarize_checks(checks)
    return {"repository": name, "target": target, "head_sha": sha, "state": state,
            "completed": len(rows) - pending, "total": len(rows), "checks": rows}


def summarize_checks(checks):
    """Reduce check runs to (rows, state, pending); shared by the single-target view and the audit."""
    rows = [{"id": x["id"], "name": x["name"], "status": x["status"],
             "conclusion": x.get("conclusion"), "url": x.get("url")}
            for x in checks]
    pending = sum(x["status"] != "completed" for x in rows)
    failed = sum(x["conclusion"] in ("failure", "timed_out", "action_required", "startup_failure") for x in rows)
    cancelled = sum(x["conclusion"] in ("cancelled", "stale") for x in rows)
    incomplete = sum(x["status"] == "completed" and x["conclusion"] in (None, "skipped", "neutral") for x in rows)
    state = "FAILED" if failed else "RUNNING" if pending else "INCOMPLETE" if cancelled or incomplete else "NO CHECKS" if not rows else "COMPLETE"
    return rows, state, pending


# ---------------------------------------------------------------- audit snapshot (producer)

STATE_LABEL = {"FAILED": "失敗", "RUNNING": "実行中", "INCOMPLETE": "不完全", "NO CHECKS": "チェックなし",
               "COMPLETE": "成功", "UNKNOWN": "不明", "-": "-"}
RECORD_STATES = (("", "未着手"), ("doing", "対応中"), ("waiting", "待ち"), ("done", "完了"))
# Check runs that are not this organization's CI (GitHub-side reviewers that fail where the feature is unavailable).
IGNORED_CHECKS = ("copilot-pull-request-reviewer",)


def organization_repositories(owner=OWNER):
    """Non-archived repository names of the owner (public API, one listing)."""
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    return sorted(x["full_name"].split("/", 1)[1] for x in ghi.repositories(owner)["repositories"]
                  if x.get("full_name") and not x.get("archived"))


def _record_id(repository, kind, number):
    return f"{repository.split('/', 1)[1]}#{number}" if kind == "pr" else f"{repository.split('/', 1)[1]}#i{number}"


def audit_repository(name, ignored_checks=IGNORED_CHECKS):
    """Open PRs (head/base/mergeable/CI state) and Issues of one repository as audit records."""
    if ghi is None:
        raise RuntimeError("GHI unavailable")
    full = repository(name)
    records = []
    pulls = ghi.pull_requests(full)
    for pr in pulls["pull_requests"]:
        detail = get(f"/repos/{full}/pulls/{pr['number']}")
        observation = ghi.checks_for_sha(full, pr["head_sha"])
        if observation["complete"]:
            rows, state, _pending = summarize_checks([x for x in observation["checks"] if x["name"] not in ignored_checks])
        else:
            rows, state = [], "UNKNOWN"
        records.append({"id": _record_id(full, "pr", pr["number"]), "repository": full.split("/", 1)[1], "number": pr["number"],
                        "kind": "pr", "title": pr["title"], "url": pr["url"], "draft": pr["draft"],
                        "head_sha": pr["head_sha"], "head_ref": pr["head_ref"], "base_ref": pr["base_ref"],
                        "mergeable": detail.get("mergeable"), "mergeable_state": detail.get("mergeable_state"),
                        "updated_at": pr["updated_at"], "ci": state,
                        "checks": [{"name": x["name"], "conclusion": x["conclusion"], "status": x["status"]} for x in rows]})
    for issue in ghi.issues(full)["issues"]:
        records.append({"id": _record_id(full, "issue", issue["number"]), "repository": full.split("/", 1)[1], "number": issue["number"],
                        "kind": "issue", "title": issue["title"], "url": issue["url"], "labels": issue["labels"],
                        "updated_at": issue["updated_at"], "ci": "-", "checks": []})
    truncated = pulls["truncated"]
    return records, truncated


def link_dependencies(records):
    """Stacked PRs: a PR whose base is another open PR's head branch depends on it; mergeable False is a conflict."""
    by_repo_head = {}
    for r in records:
        if r["kind"] == "pr":
            by_repo_head.setdefault(r["repository"], {})[r["head_ref"]] = r["id"]
    for r in records:
        if r["kind"] != "pr":
            continue
        parent = by_repo_head.get(r["repository"], {}).get(r["base_ref"])
        r["depends_on"] = [parent] if parent and parent != r["id"] else []
        r["conflict"] = r.get("mergeable") is False
    return records


def load_records(path):
    """User-maintained state/notes per record id from a JSON Lines file (one object per line)."""
    out = {}
    if path is None or not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        out[row["id"]] = {k: row[k] for k in ("state", "notes", "next", "blocker") if k in row}
    return out


def diff_snapshots(current, previous):
    """Records that are new, gone, or whose head/CI changed since the previous snapshot."""
    if not previous:
        return {"new": [], "gone": [], "changed": []}
    before = {r["id"]: r for r in previous.get("records", [])}
    after = {r["id"]: r for r in current}
    changed = [rid for rid in after if rid in before and (after[rid].get("head_sha") != before[rid].get("head_sha")
                                                             or after[rid].get("ci") != before[rid].get("ci"))]
    return {"new": sorted(set(after) - set(before)), "gone": sorted(set(before) - set(after)), "changed": sorted(changed)}


def audit(repos=None, *, records_path=None, previous=None, ignored_checks=IGNORED_CHECKS, with_conventions=True):
    """Cross-repository audit snapshot: every open PR and Issue with CI, dependencies, conflicts, user records and naming drift."""
    names = list(repos) if repos else organization_repositories()
    records, errors, truncated, findings = [], [], [], []
    for name in names:
        try:
            rows, partial = audit_repository(name, ignored_checks)
            records.extend(rows)
            if partial:
                truncated.append(name)
            if with_conventions:
                found, _truncated_tree = conventions_repository(name)
                findings.extend(found)
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            errors.append({"repository": name, "error": type(exc).__name__, "detail": _detail(exc)})
    link_dependencies(records)
    notes = load_records(records_path)
    for r in records:
        r.update({"state": "", "notes": "", **notes.get(r["id"], {})})
    records.sort(key=lambda r: (r["repository"].lower(), r["kind"], -r["number"]))
    prs = [r for r in records if r["kind"] == "pr"]
    summary = {"repositories": len(names), "pull_requests": len(prs), "issues": len(records) - len(prs),
               "ci_failed": sum(r["ci"] == "FAILED" for r in prs), "conflicts": sum(r.get("conflict") for r in prs),
               "stacked": sum(bool(r.get("depends_on")) for r in prs), "done": sum(r["state"] == "done" for r in records),
               "conventions": len(findings)}
    return {"schema": "parent-audit/1", "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "owner": OWNER, "repositories": names, "ignored_checks": list(ignored_checks), "records": records, "summary": summary,
            "conventions": findings,
            "errors": errors, "truncated": truncated, "changes": diff_snapshots(records, previous)}


def load_previous(source):
    """Previous data.json from a path or an HTTPS URL (the deployed page); missing is not an error."""
    if not source:
        return None
    if source.startswith("https://"):
        try:
            with urllib.request.urlopen(source, timeout=30) as response:  # noqa: S310 - explicit https only
                return json.loads(response.read(2_000_000).decode("utf-8"))
        except (OSError, ValueError):
            return None
    path = Path(source)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# ---------------------------------------------------------------- naming conventions (fleet-wide drift check)

# Mirrors the table in templates/README.md ("Workflow file and display names across repositories").
CONVENTIONS = {
    "workflow_names": {"ci.yml": "CI", "actionlint.yml": "Validate GitHub Actions", "runtime-compatibility.yml": "Stdlib runtime compatibility",
                       "pages.yml": "Pages", "claude.yml": "Claude", "platform-matrix.yml": "Platform matrix",
                       "py-launch-tests.yml": "py_launch tests", "openwebui-docker-smoke.yml": "OpenWebUI Docker smoke"},
    # A workflow whose display name says it plays one of these roles belongs in the canonical file.
    "role_by_name": {"ci": "ci.yml", "tests": "ci.yml", "test": "ci.yml", "claude": "claude.yml", "pages": "pages.yml"},
    "job_ids": {"test-locked": "vendor-locked", "junit-identity": "failure-identity", "resolve-aoi": "resolve-vendor",
                "deps": "resolve-vendor", "aoi": "resolve-vendor", "pytest": "test", "unittest": "test"},
    "job_names": {"vendor-locked": "Vendor locked baseline", "vendor-lock": "Vendor lock", "vendor-dispatch": "Vendor dispatch"},
    "github_files": {".github/PULL_REQUEST_TEMPLATE.md": ".github/pull_request_template.md"},
    # Generated document trees that keep their own naming (cycle logs, pattern catalogues).
    "docs_exempt": {"Aoi": ("docs/",), "flutter_navigation_basic": ("docs/",)},
    # The hub keeps several test lanes instead of one ci.yml; a child's second lane that the parent locks by digest stays.
    "ci_exempt": ("myon-bioinformatics",),
    "ci_exempt_files": {"flutter_navigation_basic": ("non-dart.yml",)},
}
_WORKFLOW_FILE = re.compile(r"[a-z0-9][a-z0-9-]*\.yml\Z")
_DOC_FILE = re.compile(r"[a-z0-9][a-z0-9.-]*\.md\Z")


def workflow_facts(text):
    """name:, job ids (2-space keys under jobs:) and workflow_run names from one workflow file, without YAML."""
    name = re.search(r"^name:\s*(.+?)\s*$", text, re.M)
    jobs = []
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\S", line):
            in_jobs = False
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line) if in_jobs else None
        if m:
            jobs.append(m.group(1))
    job_names = dict(re.findall(r"^  ([A-Za-z0-9_-]+):\s*\n(?:    .*\n)*?    name:\s*(.+?)\s*$", text, re.M))
    triggers = []
    run = re.search(r"^\s+workflow_run:\s*\n((?:\s{4,}.*\n)+)", text, re.M)
    if run:
        body = run.group(1)
        inline = re.search(r"workflows:\s*\[([^\]]*)\]", body)
        if inline:
            triggers = [x.strip().strip("'\"") for x in inline.group(1).split(",") if x.strip()]
        else:
            listed = re.search(r"workflows:\s*\n((?:\s+-\s*.+\n)+)", body)
            if listed:
                triggers = [re.sub(r"^\s*-\s*", "", x).strip().strip("'\"") for x in listed.group(1).splitlines()]
    on_block = re.search(r"^(?:on|\"on\"|'on'|true):[ \t]*(.*)\n((?:  \S.*\n|[ \t]*\n|\s{3,}.*\n)*)", text, re.M)
    events = []
    if on_block:
        inline = on_block.group(1).strip()
        if inline:
            events = [x.strip() for x in inline.strip("[]").split(",") if x.strip()]
        else:
            events = re.findall(r"^  ([A-Za-z_]+):", on_block.group(2), re.M)
    return {"name": name.group(1).strip().strip("'\"") if name else None, "jobs": jobs, "job_names": job_names,
            "workflow_run": triggers, "events": events}


def check_conventions(repo, paths, workflows):
    """Findings for one repository: paths = tracked file paths, workflows = {file name: text}."""
    rules = CONVENTIONS
    found = []
    def add(rule, path, current, expected):
        found.append({"repository": repo, "rule": rule, "path": path, "found": current, "expected": expected})
    names = {}
    for file, text in sorted(workflows.items()):
        facts = workflow_facts(text)
        names[facts["name"]] = file
        if not _WORKFLOW_FILE.fullmatch(file):
            add("workflow-file", f".github/workflows/{file}", file, "lowercase, hyphens, .yml")
        if facts["events"] == ["workflow_dispatch"]:
            add("dispatch-only", f".github/workflows/{file}", "workflow_dispatch のみ", "削除候補（手動専用）か、トリガーを足す")
        canonical = rules["workflow_names"].get(file)
        if canonical and facts["name"] != canonical:
            add("workflow-name", f".github/workflows/{file}", facts["name"], canonical)
        role = rules["role_by_name"].get((facts["name"] or "").lower())
        if not role and "actions/deploy-pages@" in text:
            role = "pages.yml"
        if not role and "ci.yml" not in workflows and file not in rules["workflow_names"] \
                and "reusable-junit-identity.yml" in text and "pull_request" in text:
            role = "ci.yml"  # no main lane yet: a junit-reporting PR lane is the candidate
        exempt_ci = role == "ci.yml" and (repo in rules["ci_exempt"] or file in rules["ci_exempt_files"].get(repo, ()))
        if role and file != role and not file.startswith("reusable-") and not exempt_ci:
            add("workflow-role", f".github/workflows/{file}", file, role)
        if file == "ci.yml":
            for job in facts["jobs"]:
                if job in rules["job_ids"]:
                    add("job-id", f".github/workflows/{file}#{job}", job, rules["job_ids"][job])
                wanted = rules["job_names"].get(job)
                if wanted and facts["job_names"].get(job) not in (None, wanted):
                    add("job-name", f".github/workflows/{file}#{job}", facts["job_names"].get(job), wanted)
    for file, text in sorted(workflows.items()):
        for target in workflow_facts(text)["workflow_run"]:
            if target not in names:
                add("workflow-run", f".github/workflows/{file}", target, "an existing workflow name")
    exempt = rules["docs_exempt"].get(repo, ())
    odd = [p for p in paths if p.startswith("docs/") and p.endswith(".md") and not p.startswith(exempt)
           and p.rsplit("/", 1)[1] != "README.md" and not _DOC_FILE.fullmatch(p.rsplit("/", 1)[1])]
    if odd:
        add("doc-file", ", ".join(odd[:5]) + (f" … (+{len(odd) - 5})" if len(odd) > 5 else ""), f"{len(odd)} file(s)", "lowercase kebab-case .md")
    for path, expected in rules["github_files"].items():
        if path in paths:
            add("github-file", path, path, expected)
    return found


def conventions_repository(name):
    """Fetch the tree and workflow texts of one repository and check them (public API)."""
    full = repository(name)
    branch = get(f"/repos/{full}")["default_branch"]
    tree = get(f"/repos/{full}/git/trees/{branch}?recursive=1")
    paths = [x["path"] for x in tree.get("tree", []) if x.get("type") == "blob"]
    workflows = {}
    for path in paths:
        if path.startswith(".github/workflows/") and path.count("/") == 2 and path.endswith((".yml", ".yaml")):
            blob = get(f"/repos/{full}/contents/{path}?ref={branch}")
            workflows[path.rsplit("/", 1)[1]] = base64.b64decode(blob.get("content") or "").decode("utf-8", "replace")
    return check_conventions(name, paths, workflows), tree.get("truncated", False)


def conventions(repos=None):
    """Fleet-wide drift report: {"repositories", "findings", "errors"}; empty findings means everything matches."""
    names = list(repos) if repos else organization_repositories()
    findings, errors = [], []
    for name in names:
        try:
            rows, truncated = conventions_repository(name)
            findings.extend(rows)
            if truncated:
                errors.append({"repository": name, "error": "TruncatedTree", "detail": "tree listing truncated; findings may be incomplete"})
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            errors.append({"repository": name, "error": type(exc).__name__, "detail": _detail(exc)})
    return {"repositories": names, "findings": findings, "errors": errors}


def render_conventions(report):
    lines = [f"{len(report['findings'])} finding(s) across {len(report['repositories'])} repositories"]
    for f in report["findings"]:
        lines.append(f"  {f['repository']:28} {f['rule']:14} {f['path']}: {f['found']!s} -> {f['expected']}")
    for e in report["errors"]:
        lines.append(f"  ! {e['repository']}: {e['error']} {e['detail']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- audit page (consumer, read-only)

def _load_web_ui():
    source = Path(__file__).resolve().parent / "vendor" / "web_ui.py"
    if not source.is_file():
        raise RuntimeError("vendored web_ui.py is not present")
    spec = importlib.util.spec_from_file_location("ci_status_web_ui", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _link(ui, text, url):
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(text)}</a>'


def _record_row(ui, r, index):
    ci = ui.badge(STATE_LABEL.get(r["ci"], r["ci"]))
    flags = []
    if r.get("conflict"):
        flags.append(ui.badge("競合"))
    for dep in r.get("depends_on", []):
        flags.append(ui.badge("先行 " + dep))
    if r.get("draft"):
        flags.append(ui.badge("draft"))
    branch = f'{r.get("head_ref", "")} → {r.get("base_ref", "")}' if r["kind"] == "pr" else ", ".join(r.get("labels", []))
    return (_link(ui, f'{r["repository"]} #{r["number"]}', r["url"]), escape(r["title"]), escape(branch), ci, " ".join(flags),
            ui.draft_select(RECORD_STATES, key=r["id"] + ":state", id=f"state-{index}", label="状態", selected=r["state"] if r["state"] in dict(RECORD_STATES) else ""),
            ui.draft_textarea(key=r["id"] + ":notes", id=f"notes-{index}", label="メモ", value=r["notes"], rows=2))


def render_audit_html(snapshot, *, asset_base=".", title="Repository audit — myon-bioinformatics"):
    """Read-only dashboard from a snapshot: queue / compare / weekly / done views built with web_ui helpers."""
    ui = _load_web_ui()
    records = snapshot["records"]
    summary = snapshot["summary"]
    columns = ("記録", "件名", "ブランチ / ラベル", "CI", "状態フラグ", "状態", "メモ")
    rows = {rid: _record_row(ui, r, i) for i, (rid, r) in enumerate((r["id"], r) for r in records)}
    def table(name, selected, caption):
        chosen = [r for r in records if selected(r)]
        return ui.data_table(columns, [rows[r["id"]] for r in chosen], id=f"table-{name}", caption=f"{caption}（{len(chosen)}件）",
                             trusted_cells=True, row_ids=tuple(r["id"] for r in chosen))
    changes = snapshot.get("changes", {})
    changed = set(changes.get("new", [])) | set(changes.get("changed", []))
    views = (("queue", "◫ 作業キュー"), ("compare", "⇄ 依存・競合"), ("weekly", "▦ 週次差分"), ("rules", "⚖ 規約ずれ"), ("done", "✓ 完了"))
    tiles = ui.stat_tiles((("リポジトリ", summary["repositories"]), ("オープンPR", summary["pull_requests"]), ("Issue", summary["issues"]),
                           ("CI失敗", summary["ci_failed"], "最新 head の check-runs"), ("競合", summary["conflicts"], "mergeable=false"),
                           ("積み上げPR", summary["stacked"], "base が他 PR の head"), ("規約ずれ", summary.get("conventions", 0), "名前の規約との差"),
                           ("完了記録", summary["done"])))
    notice = "読み取り専用の公開 API 観測（gh_identity 経由、親の ci_status.py --audit が生成）。状態・メモはこのブラウザの下書きで、JSON 書き出しを site/records.jsonl に取り込むと次回の生成に反映されます。"
    errors = "".join(f"<li>{escape(e['repository'])}: {escape(e['error'])}</li>" for e in snapshot.get("errors", []))
    head = (f'<header class="ui-panel"><h1>{escape(title)}</h1><p class="ui-muted">生成 {escape(snapshot["generated_at"])} · '
            f'{escape(snapshot["owner"])}</p>{ui.view_tabs(views, default_view="queue")}</header>'
            f'<p class="ui-muted">{escape(notice)}</p>{tiles}'
            + (f'<details class="ui-panel"><summary>取得できなかったリポジトリ</summary><ul>{errors}</ul></details>' if errors else ""))
    bar = (f'<div class="ui-records-bar">{ui.filter_input("table-queue", id="filter-queue", label="絞り込み", count_id="count-queue")}'
           f'<span id="count-queue" class="ui-muted"></span>{ui.records_io(filename="records.json")}</div>')
    queue = f'<section data-ui-view="queue">{bar}{table("queue", lambda r: r["state"] != "done", "未完了の PR / Issue")}</section>'
    compare = (f'<section data-ui-view="compare" hidden><p class="ui-muted">base が他のオープン PR の head と一致するものを依存候補、GitHub の mergeable=false を競合として表示します。</p>'
               f'{table("compare", lambda r: r["kind"] == "pr" and (r.get("depends_on") or r.get("conflict")), "依存・競合のある PR")}</section>')
    weekly = (f'<section data-ui-view="weekly" hidden><p class="ui-muted">前回の公開スナップショットと比べて新規・head/CI 変化のあった記録。消えた記録: '
              f'{escape(", ".join(changes.get("gone", [])) or "なし")}</p>{table("weekly", lambda r: r["id"] in changed, "変化した記録")}</section>')
    findings = snapshot.get("conventions", [])
    rules = (f'<section data-ui-view="rules" hidden><p class="ui-muted">templates/README.md の命名規約（ci.yml=CI、pages.yml=Pages、resolve-vendor / vendor-locked / failure-identity、docs/ はケバブケース）との差。'
             f'直すときは該当リポジトリに 1 PR（リネームだけなら他 PR より先にマージ）。</p>'
             + ui.data_table(("リポジトリ", "規約", "対象", "現状", "期待"), [(f["repository"], f["rule"], f["path"], str(f["found"]), f["expected"]) for f in findings],
                             id="table-rules", caption=f"規約ずれ（{len(findings)}件）") + "</section>")
    done = f'<section data-ui-view="done" hidden>{table("done", lambda r: r["state"] == "done", "完了記録")}</section>'
    body = ui.records_board(head + queue + compare + weekly + rules + done, id="audit")
    return ui.render_document(trusted_html=f'<main class="ui-page">{body}</main>', title=title, css=ui.bundled_css(),
                              scripts=ui.shared_scripts(asset_base, names=("navigation.js", "records.js")))


def write_audit_site(snapshot, directory):
    """Write data.json, index.html and the browser modules under directory; returns the written paths."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "index.html").write_text(render_audit_html(snapshot), encoding="utf-8")
    ui = _load_web_ui()
    (out / "js").mkdir(exist_ok=True)
    for name in ("navigation.js", "records.js"):
        (out / "js" / name).write_text(ui.js_source(name), encoding="utf-8", newline="\n")
    return ["data.json", "index.html", "js/navigation.js", "js/records.js"]

def _detail(exc):
    """Exception text for callers, with token values removed."""
    text = str(exc).replace("\n", " ")
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "***")
    return text[:500]


def render(rows):
    for item in rows:
        print(f'{item["repository"]}  {item["target"]}  {item["state"]}  {item["completed"]}/{item["total"]}  {item["head_sha"][:8]}')
        for check in item["checks"]:
            print(f'  {check["status"]:10} {str(check["conclusion"] or "-"):12} {check["name"]}')
            
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repository", nargs="?")
    p.add_argument("--pr", type=int)
    p.add_argument("--refresh", action="store_true", help="Force fresh API observation (default already fresh)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--explain", action="store_true",
                   help="include detail and hint on observation errors; default omits exception text")
    p.add_argument("--ci-check", action="store_true", help="Exit with 1 if any observed repository state is not COMPLETE or NO CHECKS")
    p.add_argument("--repos", default="Ironmate,flutter_navigation_basic,markdown,ascii_artist",
                   help="Comma-separated repositories for the all-repositories view")
    p.add_argument("--audit", action="store_true",
                   help="Snapshot every open PR/Issue of the organization (or --repos when --all-repos is not given) into data.json and a read-only index.html")
    p.add_argument("--all-repos", action="store_true", help="With --audit: enumerate the owner's non-archived repositories instead of --repos")
    p.add_argument("--records", metavar="JSONL", help="With --audit: user state/notes per record id (JSON Lines)")
    p.add_argument("--previous", metavar="PATH_OR_URL", help="With --audit: previous data.json for the weekly diff")
    p.add_argument("--out", metavar="DIR", help="With --audit: write data.json, index.html and js/ here")
    p.add_argument("--ignore-checks", default=",".join(IGNORED_CHECKS), metavar="NAMES",
                   help="With --audit: comma-separated check-run names that do not count as CI")
    p.add_argument("--conventions", action="store_true",
                   help="Report naming drift (workflow files / names, ci.yml job ids, docs/*.md) against templates/README.md; exit 1 when any")
    a = p.parse_args(argv)
    if a.conventions and not a.audit:
        names = None if a.all_repos else [x.strip() for x in a.repos.split(",") if x.strip()]
        report = conventions(names)
        print(json.dumps(report, ensure_ascii=False, indent=2) if a.json else render_conventions(report))
        return 2 if report["errors"] else 1 if report["findings"] else 0
    if a.audit:
        names = None if a.all_repos else [x.strip() for x in a.repos.split(",") if x.strip()]
        ignored = tuple(x.strip() for x in a.ignore_checks.split(",") if x.strip())
        snapshot = audit(names, records_path=a.records, previous=load_previous(a.previous), ignored_checks=ignored)
        if a.out:
            written = write_audit_site(snapshot, a.out)
            print(json.dumps({"schema": snapshot["schema"], "generated_at": snapshot["generated_at"], "summary": snapshot["summary"],
                              "written": written, "errors": snapshot["errors"]}, ensure_ascii=False))
        else:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 2 if snapshot["errors"] else 0
    if a.pr is not None and (a.pr < 1 or not a.repository):
        p.error("--pr requires a repository and positive PR number")
    names = [a.repository] if a.repository else [x.strip() for x in a.repos.split(",") if x.strip()]
    rows = []
    errors = []
    for name in names:
        try:
            rows.append(observe(name, a.pr))
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            item = {"repository": name, "error": type(exc).__name__}
            if a.explain:
                item["detail"] = _detail(exc)
                item["hint"] = ("Observation failed before a CI state was known. Exit 2 wins over --ci-check. "
                                "detail is the exception text with token values removed.")
            errors.append(item)
    output = {"schema": "ci-observation/1",
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "repositories": rows, "errors": errors}
    if a.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f'Observed at: {output["observed_at"]}')
        render(rows)
        for error in errors:
            print(f'{error["repository"]}  UNKNOWN ({error["error"]})')
            if a.explain:
                print(f'  detail: {error["detail"]}')
                print(f'  hint: {error["hint"]}')
    if errors:
        return 2
    if a.ci_check and any(r["state"] not in ("COMPLETE", "NO CHECKS") for r in rows):
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
