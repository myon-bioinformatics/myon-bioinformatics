"""Fleet entry point on synthetic local Git repositories: discovery, graph, plan, tests, report.

Standard library only (runs under ``python -S -m unittest``); no network: every
repository is a local clone, and nothing is fetched.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vendor_fleet_plan as fleet  # noqa: E402

OWNER = fleet.OWNER
ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
TEST_OK = '''import os, sys, unittest
import helper

class AppTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(helper.NAME, "{name}")

    def test_environment_is_clean(self):
        self.assertNotIn("PYTHONPATH", os.environ)
        self.assertNotIn("PYTEST_ADDOPTS", os.environ)
        self.assertFalse(any(p.rstrip("/").endswith("myon-bioinformatics") for p in sys.path if p))
'''
TEST_FAIL = '''import unittest

class Broken(unittest.TestCase):
    def test_fails(self):
        self.assertEqual(1, 2)
'''


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def commit(path, files, message="init"):
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        git(path, "init", "-q", "-b", "main")
    for name, data in files.items():
        target = path / name
        if data is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD")


def row(repository, source, destination, sha, data):
    return {"repository": f"{OWNER}/{repository}", "ref": sha, "commit": sha, "source": source, "destination": destination,
            "blob_sha": blob(data), "sha256": hashlib.sha256(data).hexdigest()}


def lock(*rows):
    return json.dumps({"schema": "vendor-lock/1", "files": list(rows)}, indent=1) + "\n"


class FleetFixture(unittest.TestCase):
    """tool → app → relay (an indirect chain), a hub with one subtree child, a legacy and an unrecorded consumer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.checkouts = base / "checkouts"
        self.parent = base / "parent"
        v1 = b"VALUE = 1\n"
        self.tool_v1 = commit(self.checkouts / "tool", {"tool.py": v1, "LICENSE": "MIT\n"})
        self.app = commit(self.checkouts / "app", {
            "vendor.lock.json": lock(row("tool", "tool.py", "vendor/tool.py", self.tool_v1, v1)),
            "vendor/tool.py": v1, "tests/helper.py": 'NAME = "app"\n', "tests/test_app.py": TEST_OK.format(name="app")})
        self.relay = commit(self.checkouts / "relay", {
            "vendor.lock.json": lock(row("app", "vendor/tool.py", "vendor/tool.py", self.app, v1)),
            "vendor/tool.py": v1, "tests/helper.py": 'NAME = "relay"\n', "tests/test_relay.py": TEST_OK.format(name="relay")})
        self.child = commit(self.checkouts / "child", {"README.md": "child\n"})
        self.hub = commit(self.checkouts / "hub", {
            "child-heads.json": json.dumps({"schema": "child-heads/1", "ref": "main", "heads": {
                f"{OWNER}/child": {"sha": self.child, "repository_id": 7, "optional": {"subtree_prefix": "child"}}}}),
            "child/README.md": "child\n", "tests/test_hub.py": TEST_FAIL})
        commit(self.checkouts / "stray", {"vendor.lock.json": lock(row("tool", "tool.py", "vendor/tool.py", self.tool_v1, v1)),
                                          "vendor/tool.py": v1})
        registry = {"schema": "vendor-consumers/1", "consumers": [
            {"repository": f"{OWNER}/app", "state": "locked", "lock": "vendor.lock.json",
             "entries": [{"repository": f"{OWNER}/tool", "source": "tool.py", "destination": "vendor/tool.py"}]},
            {"repository": f"{OWNER}/relay", "state": "locked", "lock": "vendor.lock.json",
             "entries": [{"repository": f"{OWNER}/app", "source": "vendor/tool.py", "destination": "vendor/tool.py"}]},
            {"repository": f"{OWNER}/hub", "state": "locked", "lock": "vendor.lock.json",
             "entries": [{"repository": f"{OWNER}/tool", "source": "tool.py", "destination": "vendor/tool.py"}]},
            {"repository": f"{OWNER}/old", "state": "legacy", "lock": None, "reason": "repository_archived",
             "legacy_provenance": ["vendor/tool.provenance.json"],
             "entries": [{"repository": f"{OWNER}/tool", "source": "tool.py", "destination": "vendor/tool.py"}]}]}
        heads = {"schema": "child-heads/1", "ref": "main", "heads": {
            f"{OWNER}/app": {"sha": self.app, "repository_id": 1}, f"{OWNER}/relay": {"sha": self.relay, "repository_id": 2},
            f"{OWNER}/hub": {"sha": self.hub, "repository_id": 3}}}
        catalog = {"schema": "vendor-catalog/1", "tools": [
            {"repository": f"{OWNER}/tool", "source": "tool.py", "commit": self.tool_v1, "reason": "reviewed"}]}
        commit(self.parent, {"vendor-consumers.json": json.dumps(registry), "child-heads.json": json.dumps(heads),
                             "vendor-catalog.json": json.dumps(catalog),
                             ".github/workflows/ci.yml": "name: CI\non: [push]\njobs:\n  a:\n    runs-on: x\n    steps: []\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def fleet(self, **kwargs):
        return fleet.Fleet(self.parent, self.checkouts, **kwargs)

    def analysed(self, **kwargs):
        f = self.fleet(**kwargs)
        records = fleet.load_records(f)
        discovery = fleet.discover(f, records)
        return f, discovery, fleet.build_graph(f, discovery, records)


class DiscoveryAndGraph(FleetFixture):
    def test_targets_excluded_and_insufficient_come_from_existing_records(self):
        _f, discovery, _g = self.analysed()
        by = {r["repository"]: r for r in discovery["repositories"]}
        self.assertEqual(by["app"]["category"], "target")
        self.assertEqual(by["child"]["category"], "target")  # declared by the hub's own child-heads.json
        self.assertEqual(by["child"]["hubs"]["hub"]["prefix"], "child")
        self.assertEqual(by["old"]["category"], "excluded")
        self.assertIn("legacy consumer", by["old"]["reasons"][0])
        self.assertEqual(by["hub"]["category"], "insufficient")
        self.assertIn("registered lock missing: vendor.lock.json", by["hub"]["reasons"])
        self.assertEqual(by["stray"]["category"], "insufficient")
        self.assertIn("lock outside every hub", by["stray"]["reasons"][0])

    def test_graph_edges_indirect_impact_and_no_cycle(self):
        _f, _d, graph = self.analysed()
        pairs = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        self.assertIn(("tool:tool.py", "app:vendor/tool.py", "vendor"), pairs)
        self.assertIn(("app:vendor/tool.py", "relay:vendor/tool.py", "vendor"), pairs)
        self.assertIn(("hub:child/", "child:", "subtree"), pairs)
        self.assertIn(("tool:tool.py", f"{fleet.PARENT}:vendor-catalog.json", "catalog"), pairs)
        self.assertEqual(graph["cycles"], [])
        self.assertLess(graph["order"].index("app:vendor/tool.py"), graph["order"].index("relay:vendor/tool.py"))
        reach = fleet.impact(graph, ["tool:tool.py"])
        depth = {r["node"]: r["depth"] for r in reach["rows"]}
        self.assertEqual(depth["app:vendor/tool.py"], 1)
        self.assertEqual(depth["relay:vendor/tool.py"], 2)
        self.assertEqual(reach["repositories"]["relay"], {"direct": False, "indirect": True})
        self.assertEqual(fleet.impact(graph, ["nowhere:x.py"])["unknown"], ["nowhere:x.py"])

    def test_mutual_reference_is_information_but_a_file_cycle_is_an_error(self):
        data = b"VALUE = 1\n"
        commit(self.checkouts / "tool", {"vendor.lock.json": lock(row("app", "vendor/tool.py", "tool.py", self.app, data))})
        _f, _d, graph = self.analysed()
        self.assertIn(["app", "tool"], [m["repositories"] for m in graph["mutual"] if m["kind"] == "code"])
        self.assertTrue(graph["cycles"])  # tool.py feeds app's copy, which feeds tool.py again
        self.assertEqual(graph["order"], [])

    def test_heads_mode_reads_the_recorded_revisions(self):
        commit(self.checkouts / "app", {"README.md": "after the recorded head\n"})
        _f, discovery, _g = self.analysed(at="heads")
        by = {r["repository"]: r for r in discovery["repositories"]}
        self.assertEqual(by["app"]["revision"], self.app)
        self.assertEqual(by["tool"]["revision"], self.tool_v1)  # not recorded: its main
        self.assertEqual(by[fleet.PARENT]["revision"], fleet.WORKTREE)

    def test_reads_are_cached_within_one_run(self):
        f, discovery, _g = self.analysed()
        calls = f.stats["git_calls"]
        fleet.build_graph(f, discovery)
        self.assertEqual(f.stats["git_calls"], calls)
        self.assertGreater(f.stats["cache_hits"], 0)


class Plans(FleetFixture):
    def test_moved_commit_with_identical_content_is_skipped(self):
        commit(self.checkouts / "tool", {"README.md": "docs only\n"})
        f, d, g = self.analysed()
        planned = fleet.plan_updates(f, g, d)
        rows = {r["id"]: r for r in planned["rows"]}
        app = rows["vendor:app:vendor/tool.py<-tool:tool.py"]
        self.assertEqual(app["status"], "skipped")
        self.assertIn("same content", app["reason"])
        self.assertFalse([s for s in planned["steps"] if s["phase"] == "vendor"])

    def test_changed_source_orders_vendor_verify_then_downstream_and_records(self):
        new = commit(self.checkouts / "tool", {"tool.py": "VALUE = 2\n"})
        f, d, g = self.analysed()
        planned = fleet.plan_updates(f, g, d, changed=["tool:tool.py"])
        steps = {s["id"]: s for s in planned["steps"]}
        first = steps["vendor:app:vendor.lock.json:tool"]
        self.assertTrue(first["auto"])
        self.assertEqual(first["argv"][3:], ["update", "--root", "<checkouts>/app", "--manifest", "vendor.lock.json",
                                             "--only", "vendor/tool.py", "--to-ref", new])
        self.assertEqual(first["argv"][2], "<tools>/vendor_sync.py")
        downstream = steps["vendor:relay:vendor.lock.json:app"]
        self.assertEqual(downstream["deps"], ["verify:app"])
        self.assertFalse(downstream["auto"])
        self.assertLess(first["wave"], steps["verify:app"]["wave"])
        self.assertLess(steps["verify:app"]["wave"], downstream["wave"])
        self.assertIn("verify:relay", steps[f"record:{fleet.PARENT}:child-heads"]["deps"])
        self.assertEqual(steps["record:catalog"]["phase"], "record")

    def test_apply_holds_dependents_of_manual_steps(self):
        commit(self.checkouts / "tool", {"tool.py": "VALUE = 2\n"})
        f, d, g = self.analysed()
        planned = fleet.plan_updates(f, g, d, changed=["tool:tool.py"])
        for step in planned["steps"]:  # no network here: make every automatic step manual
            step["auto"] = False
        rows = {r["id"]: r for r in fleet.apply_plan(f, planned, d)}
        self.assertEqual(rows["vendor:app:vendor.lock.json:tool"]["status"], "skipped")
        self.assertEqual(rows["verify:app"]["status"], "blocked")
        self.assertIn("waits for vendor:app:vendor.lock.json:tool", rows["verify:app"]["reason"])


class CrossRepositoryTests(FleetFixture):
    def run_tests(self, **kwargs):
        f, d, _g = self.analysed()
        return {r["id"]: r for r in fleet.execute(f, fleet.test_jobs(f, d, **kwargs), workers=4, timeout=120)}

    def test_same_named_tests_packages_run_isolated_and_in_parallel(self):
        rows = self.run_tests(repos=["app", "relay"])
        self.assertEqual(rows["check:app"]["status"], "success")
        self.assertEqual(rows["test:app:tests"]["status"], "success", rows["test:app:tests"])
        self.assertEqual(rows["test:relay:tests"]["status"], "success", rows["test:relay:tests"])
        self.assertEqual(rows["test:app:tests"]["summary"]["run"], 2)
        self.assertEqual(rows["test:app:tests"]["revision"], self.app)
        self.assertEqual(rows["test:app:tests"]["argv"][:4], ["python", "-I", "-S", "<tools>/vendor_fleet_plan.py"])
        self.assertEqual(git(self.checkouts / "app", "worktree", "list").count("\n"), 0)  # disposable worktrees are gone

    def test_failed_prerequisite_blocks_and_failures_are_not_passes(self):
        commit(self.checkouts / "app", {"vendor/tool.py": "edited\n"})
        rows = self.run_tests(repos=["app", "hub"])
        self.assertEqual(rows["check:app"]["status"], "failure")
        self.assertEqual(rows["test:app:tests"]["status"], "blocked")
        self.assertIn("check:app (failure)", rows["test:app:tests"]["reason"])
        self.assertEqual(rows["test:hub:tests"]["status"], "failure")

    def test_selection_by_name_pattern_list_and_zero_tests(self):
        rows = self.run_tests(repos=["app"], keywords=["environment"], checks=False)
        self.assertEqual(rows["test:app:tests"]["summary"]["run"], 1)
        listed = self.run_tests(repos=["app"], list_only=True)
        self.assertEqual(listed["test:app:tests"]["summary"]["ids"], ["test_app.AppTests.test_environment_is_clean",
                                                                      "test_app.AppTests.test_value"])
        self.assertNotIn("check:app", listed)
        none = self.run_tests(repos=["app"], keywords=["no_such_test"], checks=False)
        self.assertEqual(none["test:app:tests"]["status"], "skipped")
        missing = self.run_tests(repos=["child"])
        self.assertEqual(missing["test:child"]["status"], "skipped")

    def test_environment_overrides_do_not_leak(self):
        old = {k: os.environ.get(k) for k in ("PYTHONPATH", "PYTEST_ADDOPTS")}
        os.environ.update(PYTHONPATH=str(ROOT), PYTEST_ADDOPTS="-x")
        try:
            rows = self.run_tests(repos=["app"], checks=False)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rows["test:app:tests"]["status"], "success", rows["test:app:tests"])


class Reports(FleetFixture):
    def test_report_rows_staleness_diff_and_no_machine_paths(self):
        f = self.fleet()
        first = fleet.report(f, tests=fleet.execute(f, fleet.test_jobs(f, fleet.discover(f), repos=["relay"], checks=False)))
        self.assertEqual(first["schema"], fleet.REPORT_SCHEMA)
        self.assertEqual(set(first["counts"]), set(fleet.STATUSES))
        self.assertTrue(any(r["id"] == "test:relay:tests" and r["status"] == "success" for r in first["rows"]))
        text = json.dumps(first)
        self.assertNotIn(str(self.checkouts), text)
        self.assertNotIn(str(self.parent), text)
        # Same revision: the test row is carried over. Moved revision: it becomes stale, never current.
        carried = fleet.report(self.fleet(), previous=first)
        self.assertTrue(any(r["id"] == "test:relay:tests" and r.get("carried_from") for r in carried["rows"]))
        commit(self.checkouts / "relay", {"README.md": "moved\n"})
        moved = fleet.report(self.fleet(), previous=first)
        self.assertFalse(any(r["id"] == "test:relay:tests" for r in moved["rows"]))
        self.assertTrue(any(r["id"] == "test:relay:tests" and r["stale"] for r in moved["stale"]))
        commit(self.checkouts / "tool", {"tool.py": "VALUE = 3\n"})
        later = fleet.report(self.fleet(), previous=moved)
        self.assertIn({"id": "vendor:app:vendor/tool.py<-tool:tool.py", "from": "skipped", "to": "updated"},
                      later["diff"]["changed"])
        self.assertTrue(any(x["what"] == "📥 tool:tool.py" and x["no"].startswith("FLEET-") for x in later["remaining"]))
        for fmt in ("text", "markdown"):
            self.assertIn("🎯", fleet.render(later, fmt))

    def test_cli_exit_codes_and_formats(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-S", str(ROOT / "vendor_fleet_plan.py"), *args, "--root", str(self.parent),
                                   "--checkouts", str(self.checkouts)], capture_output=True, text=True)
        self.assertEqual(cli("graph").returncode, 0)
        self.assertEqual(cli("targets", "--check").returncode, 1)  # hub and stray are insufficient
        out = cli("impact", "tool:tool.py", "--format", "json")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["impact"]["repositories"]["relay"]["indirect"], True)
        commit(self.checkouts / "tool", {"tool.py": "VALUE = 2\n"})
        self.assertEqual(cli("plan", "--check").returncode, 1)
        self.assertEqual(cli("test", "--repos", "hub").returncode, 1)
        self.assertEqual(cli("plan", "--at", "nope").returncode, 2)


class Ontology(FleetFixture):
    def test_node_classes_and_codebook_drive_the_graph(self):
        self.assertEqual([fleet.node_class(n) for n in ("app:", "hub:child/", "app:vendor.lock.json", "app:.github/workflows/ci.yml",
                                                         "app:vendor/tool.py")], ["Repository", "Directory", "Record", "Workflow", "File"])
        self.assertEqual(fleet.UPDATE_KINDS, ("vendor", "launcher", "license"))
        self.assertEqual(len({r[0] for r in fleet.RELATIONS.values()}), len(fleet.RELATIONS))  # one code per relation
        _f, discovery, graph = self.analysed()
        self.assertEqual([f for f in graph["findings"] if f["kind"] in ("shape", "two-sources", "unknown-repository")], [])
        by = {r["repository"]: r for r in discovery["repositories"]}
        self.assertEqual((by["app"]["repository_id"], by["child"]["repository_id"]), (1, 7))

    def test_shapes_find_two_origins_unknown_repositories_and_split_identities(self):
        v1 = b"VALUE = 1\n"
        commit(self.checkouts / "app", {"tools.lock/vendor.lock.json": lock(row("ghost", "tool.py", "vendor/tool.py", self.tool_v1, v1))})
        heads = json.loads((self.checkouts / "hub" / "child-heads.json").read_text())
        heads["heads"][f"{OWNER}/app-renamed"] = {"sha": self.app, "repository_id": 1}
        heads["heads"][f"{OWNER}/relay"] = {"sha": self.relay, "repository_id": 99}
        commit(self.checkouts / "hub", {"child-heads.json": json.dumps(heads)})
        _f, discovery, graph = self.analysed()
        kinds = {(f["kind"], f["repository"]) for f in graph["findings"]}
        self.assertIn(("two-sources", "app"), kinds)  # vendor/tool.py from tool and from ghost
        self.assertIn(("unknown-repository", "ghost"), kinds)
        self.assertIn(("same-repository-two-names", "app"), kinds)  # repository_id 1 as app and app-renamed
        relay = next(r for r in discovery["repositories"] if r["repository"] == "relay")
        self.assertIn("identity conflict: repository_id 2 ≠ 99", relay["reasons"])

    def test_facts_are_lossless_keep_identifiers_and_keep_their_numbers(self):
        commit(self.checkouts / "tool", {"tool.py": "VALUE = 2\n"})
        commit(self.checkouts / "stray", {"README.md": "x\n"})
        first = fleet.report(self.fleet())
        packed = first["compression"]
        self.assertTrue(packed["roundtrip"])
        self.assertEqual(packed["missing_identifiers"], [])
        self.assertLess(packed["fact_bytes"], packed["raw_bytes"])
        rows = [r for r in first["rows"] if r["status"] == "updated"]
        self.assertEqual(fleet.expand_facts(fleet.compress_rows(rows)), rows)
        self.assertIn("vendor/tool.py", fleet.kept_identifiers(rows, "nothing here"))
        self.assertEqual(fleet.kept_identifiers([{"target": "a:.github/workflows/ci.yml"}], "a (⚙️ci.yml)"), [])
        item = next(x for x in first["remaining"] if x["what"] == "📥 tool:tool.py")
        self.assertIn("app (vendor/tool.py)", item["detail"])
        number = item["no"]
        new = commit(self.checkouts / "relay", {"README.md": "moved\n"})
        self.assertTrue(new)
        second = fleet.report(self.fleet(), previous=first)
        again = next(x for x in second["remaining"] if x["key"] == item["key"])
        self.assertEqual(again["no"], number)
        used = {x["no"] for x in second["remaining"]}
        self.assertEqual(len(used), len(second["remaining"]))

    def test_jsonld_carries_the_ontology_nodes_and_provenance(self):
        result = fleet.report(self.fleet())
        doc = fleet.to_jsonld(result)
        context, graph = doc["@context"], doc["@graph"]
        for code, *_ in fleet.RELATIONS.values():
            self.assertEqual(context[code]["@type"], "@id")
        terms = {g["@id"]: g for g in graph if g.get("@type") in ("rdfs:Class", "rdf:Property")}
        self.assertEqual(terms[fleet.VOCAB + "vendor"]["subPropertyOf"], "prov:wasDerivedFrom")
        nodes = {g["@id"]: g for g in graph if "repository" in g and g.get("@type") != "Dependency"}
        self.assertEqual(nodes["app/vendor/tool.py"]["vd"], ["tool/tool.py"])
        self.assertEqual(nodes["app/"]["repositoryId"], 1)
        statement = next(g for g in graph if g.get("@type") == "Dependency" and g["target"] == "app/vendor/tool.py")
        self.assertEqual((statement["relation"], statement["assertedBy"], statement["pin"]), ("vd", "app/vendor.lock.json", self.tool_v1))
        text = json.dumps(doc)
        self.assertNotIn(str(self.checkouts), text)
        self.assertNotIn(str(self.parent), text)


class Queries(FleetFixture):
    def document(self):
        heads = json.loads((self.parent / "child-heads.json").read_text())
        heads["heads"][f"{OWNER}/app"]["optional"] = {"previous_names": [f"{OWNER}/old-app"]}
        commit(self.parent, {"child-heads.json": json.dumps(heads)}, "record a previous name")
        commit(self.checkouts / "tool", {"tool.py": "VALUE = 2\n"})
        return fleet.to_jsonld(fleet.report(self.fleet()))

    def test_who_resolves_names_previous_names_and_ids(self):
        doc = self.document()
        by_name = fleet.query_who(doc, "app")
        self.assertEqual((by_name[0]["repository"], by_name[0]["repository_id"], by_name[0]["matched_by"]), ("app", 1, "name"))
        self.assertEqual(by_name[0]["previous_names"], ["old-app"])
        self.assertEqual(fleet.query_who(doc, f"{OWNER}/old-app")[0]["matched_by"], "previous name")
        self.assertEqual(fleet.query_who(doc, "7")[0]["repository"], "child")
        self.assertEqual(fleet.query_who(doc, "nobody"), [])

    def test_deps_group_by_relation_and_accept_a_previous_name(self):
        doc = self.document()
        rows = fleet.query_deps(doc, "old-app")
        self.assertEqual([(r["relation"], r["source"], r["target"]) for r in rows if r["relation"] == "vendor"],
                         [("vendor", "tool/tool.py", "app/vendor/tool.py")])
        self.assertTrue(any(r["relation"] == "heads" and r.get("note") for r in rows))
        self.assertEqual(rows[0]["asserted_by"], f"{fleet.PARENT}/child-heads.json")

    def test_facts_filter_by_text_and_severity_and_query_dispatches(self):
        doc = self.document()
        facts = fleet.query_facts(doc, "tool.py")
        self.assertTrue(facts and all(f["no"].startswith("FLEET-") for f in facts))
        self.assertEqual(fleet.query_facts(doc, "tool.py", severity="failure"), [])
        result = fleet.query("facts", "tool", document=doc)
        self.assertEqual((result["schema"], result["kind"], len(result["rows"]) > 0), ("vendor-fleet-query/1", "facts", True))
        with self.assertRaises(ValueError):
            fleet.query("nope", document=doc)
        for fmt in ("text", "markdown", "json"):
            self.assertIn("FLEET-", fleet.render_query(result, fmt))

    def test_cli_reads_a_file_and_exits_1_when_nothing_matches(self):
        doc = self.document()
        path = Path(self.tmp.name) / "fleet.jsonld"
        path.write_text(json.dumps(doc), encoding="utf-8")
        out = subprocess.run([sys.executable, "-S", str(ROOT / "vendor_fleet_plan.py"), "query", "deps", "app", "--graph", str(path)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("tool/tool.py", out.stdout)
        none = subprocess.run([sys.executable, "-S", str(ROOT / "vendor_fleet_plan.py"), "query", "who", "nobody", "--graph", str(path)],
                              capture_output=True, text=True)
        self.assertEqual(none.returncode, 1)


class Worker(unittest.TestCase):
    def test_worker_uses_loader_names_and_name_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tests").mkdir()
            (Path(tmp) / "tests" / "test_w.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): pass\n")
            out = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "vendor_fleet_plan.py"), "unittest-worker",
                                  "--start", "tests", "--module", "test_w", "-k", "test_b"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            summary = json.loads(out.stdout.split(fleet.RESULT_MARK, 1)[1])
            self.assertEqual((summary["run"], summary["selected"]), (1, 1))
            missing = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "vendor_fleet_plan.py"), "unittest-worker",
                                      "--start", "tests", "--module", "test_missing"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 1)
            self.assertTrue(json.loads(missing.stdout.split(fleet.RESULT_MARK, 1)[1])["load_errors"])

    def test_only_a_missing_third_party_module_triggers_the_environment_rerun(self):
        self.assertEqual(fleet._missing_package([{"stderr": "ModuleNotFoundError: No module named 'polars'"}]), "polars")
        self.assertIsNone(fleet._missing_package([{"stderr": "ModuleNotFoundError: No module named 'json.missing'"}]))
        self.assertIsNone(fleet._missing_package([{"stdout": "AssertionError: 1 != 2"}]))

    def test_classification_does_not_import(self):
        self.assertEqual(fleet._classify("import unittest\nclass A(unittest.TestCase): pass\n", set()), ("unittest", False))
        self.assertEqual(fleet._classify("import pytest\ndef test_x(): pass\n", set()), ("pytest", True))
        self.assertEqual(fleet._classify("import yaml, unittest\nclass A(unittest.TestCase): pass\n", set()), ("unittest", True))
        self.assertEqual(fleet._classify("import helper, unittest\nclass A(unittest.TestCase): pass\n", {"helper"}),
                         ("unittest", False))


if __name__ == "__main__":
    unittest.main()
