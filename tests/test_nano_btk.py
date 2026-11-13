"""nano_btk: snapshot contracts and the Node26-only profiles and parser updates (stdlib only, runs under python -S)."""

import concurrent.futures
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


# ═══ · nano btk ══════════════════════════════════════════════════════════════════════════════════════════
# Fixed BTK graph/failure tests; fake npm, no installs or browser execution.

SPEC = importlib.util.spec_from_file_location("nano_node_btk", Path(__file__).parents[1] / "tools/nano_node.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)


class BtkNodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.output = self.root / "source", self.root / "out"
        self.source.mkdir()
        self.manifest = {"private": True, "engines": {"node": ">=24"},
                         "devDependencies": {**nano.BTK_SELECTED, "exceljs": "^4.4.0"},
                         "overrides": {"uuid": "^14.0.3", "minimatch": "^11.0.1"}}
        self.lock = {"name": "dep-browser-test-kit", "lockfileVersion": 3, "requires": True,
                     "packages": {"": {"devDependencies": self.manifest["devDependencies"],
                                           "engines": {"node": ">=24"}}}}
        for path in nano.BTK_EXPECTED_PATHS | {"node_modules/exceljs"}:
            self.lock["packages"][path] = {"version": "1.0.0", "dev": True,
                "resolved": "https://registry.npmjs.org/test/-/test-1.0.0.tgz", "integrity": "sha512-fixture"}
        self.lock["packages"]["node_modules/linkedom"].update(
            peerDependencies={"canvas": ">= 2"}, peerDependenciesMeta={"canvas": {"optional": True}})
        data = {"package.json": json.dumps(self.manifest).encode(),
                "package-lock.json": json.dumps(self.lock).encode(), "LICENSE": b"test license\n"}
        for name, content in data.items():
            (self.source / name).write_bytes(content)
        self.hashes = {name: nano.git_blob(content) for name, content in data.items()}
        self.patcher = patch.object(nano, "BTK_SOURCE_BLOBS", self.hashes)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.npm = self.root / "npm"
        self.fake()

    def fake(self, mode="good"):
        script = '''#!/usr/bin/env python3
import json, pathlib, sys
if sys.argv[1:] == ["--version"]:
    print("11.9.0-test"); sys.exit(0)
assert "--offline" in sys.argv and "--package-lock-only" in sys.argv
assert "--ignore-scripts" in sys.argv
if MODE == "fail": sys.exit(2)
p=pathlib.Path("package-lock.json")
d=json.loads(p.read_text()); d["name"]="browser-test-kit"
d["packages"]={k:v for k,v in d["packages"].items() if not k or k in PATHS}
d["packages"][""]["devDependencies"]=SELECTED
if MODE == "peer": d["packages"]["node_modules/linkedom"]["peerDependenciesMeta"]["canvas"]["optional"]=False
if MODE == "missing": del d["packages"]["node_modules/marked"]
if MODE == "version": d["packages"]["node_modules/markdown-it"]["version"]="99.0.0"
if MODE == "canvas": d["packages"]["node_modules/canvas"]={"version":"3.0.0"}
p.write_text(json.dumps(d))
'''
        script = script.replace("import json, pathlib, sys", "import json, pathlib, sys\nMODE=" + repr(mode)
                                + "\nPATHS=" + repr(set(nano.BTK_EXPECTED_PATHS)) + "\nSELECTED=" + repr(nano.BTK_SELECTED))
        self.npm.write_text(script)
        self.npm.chmod(0o755)

    def prepare(self):
        return nano.prepare(self.source, self.output, str(self.npm), feature=nano.BTK_FEATURE)

    def test_success_retains_both_readers_runner_and_exact_optional_peer(self):
        result = self.prepare()
        r = json.loads((result / "receipt.json").read_text())
        projected = json.loads((result / "browser-test-kit/package-lock.json").read_text())
        self.assertEqual(r["candidate_locked_package_records"], 30)
        self.assertEqual(r["source_commit"], nano.BTK_COMMIT)
        self.assertEqual(r["feature"], nano.BTK_FEATURE)
        self.assertIsNone(r["installed_bytes"])
        self.assertEqual(projected["packages"][""]["devDependencies"], nano.BTK_SELECTED)
        self.assertEqual(projected["packages"]["node_modules/linkedom"], self.lock["packages"]["node_modules/linkedom"])
        self.assertNotIn("node_modules/canvas", projected["packages"])
        self.assertFalse((result / "browser-test-kit/node_modules").exists())
        self.assertEqual(r["preserved_overrides"], self.manifest["overrides"])

    def test_wrong_blob_rejected_before_output(self):
        (self.source / "LICENSE").write_text("changed")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_dependency_only_ignores_but_never_stages_live_source(self):
        # A later live test/source edit must neither become a historical staged
        # test nor require a parent manifest-pin refresh.
        (self.source / "LICENSE").write_text("changed live checkout")
        (self.source / "tests").mkdir()
        (self.source / "tests/new.spec.ts").write_text("throw new Error('do not execute')")
        result = nano.prepare(self.source, self.output, str(self.npm),
                              feature=nano.BTK_FEATURE, dependencies_only=True)
        self.assertEqual({p.name for p in (result / "browser-test-kit").iterdir()},
                         {"package.json", "package-lock.json"})
        receipt = json.loads((result / "receipt.json").read_text())
        self.assertEqual(set(receipt["source_git_blobs"]), {"package.json", "package-lock.json"})
        self.assertTrue(receipt["dependencies_only"])
        self.assertFalse(receipt["application_source_staged"])
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()  # The existing full-snapshot default stays strict.

    def test_dependency_only_still_rejects_changed_package_inputs(self):
        (self.source / "package.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            nano.prepare(self.source, self.output, str(self.npm),
                         feature=nano.BTK_FEATURE, dependencies_only=True)
        self.assertFalse(self.output.exists())

    def test_only_exact_linkedom_optional_canvas_peer_allowed(self):
        for mode in ("required", "version", "extra", "foreign", "canvas", "dev"):
            value = json.loads(json.dumps(self.lock))
            link = value["packages"]["node_modules/linkedom"]
            if mode == "required": link["peerDependenciesMeta"]["canvas"]["optional"] = False
            if mode == "version": link["peerDependencies"]["canvas"] = ">= 3"
            if mode == "extra": link["peerDependencies"]["other"] = "*"
            if mode == "foreign": value["packages"]["node_modules/marked"]["peerDependencies"] = {"canvas": ">= 2"}
            if mode == "canvas": value["packages"]["node_modules/canvas"] = dict(value["packages"]["node_modules/marked"])
            if mode == "dev": link["dev"] = False
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                nano.validate_original(self.manifest, value, feature=nano.BTK_FEATURE)

    def test_projection_change_or_failure_cleans_own_output(self):
        for mode in ("fail", "peer", "missing", "version", "canvas"):
            self.fake(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(list(self.output.iterdir()), [])

    def test_parallel_calls_are_isolated(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.prepare(), range(2)))
        self.assertNotEqual(*results)
        self.assertEqual(set(self.output.iterdir()), set(results))

    def test_unknown_feature_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, "unsupported named feature"):
            nano.prepare(self.source, self.output, str(self.npm), feature="all-peers")
        self.assertFalse(self.output.exists())


# ═══ · nano btk node26 ═══════════════════════════════════════════════════════════════════════════════════
# Node26-only snapshot contracts; fake npm, no installs or network.

NANO_BTK_NODE26_SPEC = importlib.util.spec_from_file_location('nano_node26', ROOT / 'tools/nano_node.py')
nano_nano_btk_node26 = importlib.util.module_from_spec(NANO_BTK_NODE26_SPEC)
NANO_BTK_NODE26_SPEC.loader.exec_module(nano_nano_btk_node26)
SOURCE = ROOT / 'tests/fixtures/btk-node26'


class Node26Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((SOURCE / 'package.json').read_bytes())
        self.lock = json.loads((SOURCE / 'package-lock.json').read_bytes())

    def projected(self):
        value = copy.deepcopy(self.lock)
        value['name'] = 'browser-test-kit'
        value['packages'] = {k: v for k, v in value['packages'].items() if not k or k in nano_nano_btk_node26.BTK_NODE26_EXPECTED_PATHS}
        value['packages']['']['devDependencies'] = nano_nano_btk_node26.BTK_NODE26_SELECTED
        return value

    def test_exact_inputs_and_unchanged_dependency_versions(self):
        nano_nano_btk_node26.read_snapshot(SOURCE, blobs=nano_nano_btk_node26.BTK_NODE26_SOURCE_BLOBS)
        nano_nano_btk_node26.validate_original(self.manifest, self.lock, feature=nano_nano_btk_node26.BTK_NODE26_FEATURE)
        self.assertEqual(nano_nano_btk_node26.BTK_NODE26_SELECTED, nano_nano_btk_node26.BTK_SELECTED)
        self.assertEqual(nano_nano_btk_node26.BTK_NODE26_EXPECTED_PATHS, nano_nano_btk_node26.BTK_EXPECTED_PATHS)
        self.assertEqual(len(nano_nano_btk_node26.BTK_NODE26_EXPECTED_PATHS), 30)
        self.assertEqual(self.lock['packages']['node_modules/marked']['version'], '18.1.1')
        self.assertEqual(self.lock['packages']['node_modules/linkedom']['version'], '0.18.13')
        self.assertEqual(self.lock['packages']['node_modules/exceljs']['version'], '4.4.0')

    def test_historical_selector_remains_exact_and_rejects_node26_metadata(self):
        self.assertEqual(nano_nano_btk_node26.BTK_SOURCE_BLOBS['package.json'], '9dbada6ad25af5051818d4399796d8d2cfc400c6')
        self.assertEqual(nano_nano_btk_node26.BTK_SOURCE_BLOBS['package-lock.json'], 'b0191e266c3516596cb4c6a9ad69b47628551145')
        with self.assertRaisesRegex(ValueError, 'blob mismatch'):
            nano_nano_btk_node26.read_snapshot(SOURCE, blobs={k: nano_nano_btk_node26.BTK_SOURCE_BLOBS[k] for k in nano_nano_btk_node26.BTK_NODE26_SOURCE_BLOBS})

    def test_projection_preserves_full_records_and_node26_floor(self):
        projected = self.projected()
        nano_nano_btk_node26.validate_projection(self.lock, projected, feature=nano_nano_btk_node26.BTK_NODE26_FEATURE)
        self.assertEqual(projected['packages']['']['engines'], {'node': '>=26'})
        for path, value in projected['packages'].items():
            if path:
                self.assertEqual(value, self.lock['packages'][path])

    def test_changed_versions_peer_or_engine_rejected(self):
        for mode in ('version', 'peer', 'engines', 'extra'):
            projected = self.projected()
            if mode == 'version': projected['packages']['node_modules/marked']['version'] = '18.1.2'
            if mode == 'peer': projected['packages']['node_modules/linkedom']['peerDependenciesMeta']['canvas']['optional'] = False
            if mode == 'engines': projected['packages']['']['engines'] = {'node': '>=24'}
            if mode == 'extra': projected['packages']['node_modules/canvas'] = {}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                nano_nano_btk_node26.validate_projection(self.lock, projected, feature=nano_nano_btk_node26.BTK_NODE26_FEATURE)

    def test_original_node_floor_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['engines'] = {'node': '>=24'}
        with self.assertRaisesRegex(ValueError, 'Node 26 engines'):
            nano_nano_btk_node26.validate_original(manifest, self.lock, feature=nano_nano_btk_node26.BTK_NODE26_FEATURE)

    def test_requires_dependency_only_and_ignores_no_guard(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(nano_nano_btk_node26.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'requires --dependencies-only'):
                nano_nano_btk_node26.prepare(SOURCE, Path(tmp), feature=nano_nano_btk_node26.BTK_NODE26_FEATURE)
            run.assert_not_called()

    def test_prepare_receipt_retains_real_source_and_no_install_claim(self):
        def fake_npm(argv, **kwargs):
            if argv[1:] == ['--version']:
                return subprocess.CompletedProcess(argv, 0, '11.9.0-test\n', '')
            self.assertEqual(argv[1:7], ['install', '--package-lock-only', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
            project = Path(kwargs['cwd'])
            self.assertEqual({p.name for p in project.iterdir()}, {'package.json', 'package-lock.json'})
            (project / 'package-lock.json').write_text(json.dumps(self.projected()))
            return subprocess.CompletedProcess(argv, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(nano_nano_btk_node26.shutil, 'which', return_value=sys.executable), patch.object(nano_nano_btk_node26.subprocess, 'run', side_effect=fake_npm):
            output = nano_nano_btk_node26.prepare(SOURCE, Path(tmp), feature=nano_nano_btk_node26.BTK_NODE26_FEATURE, dependencies_only=True)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['source_commit'], '8de4a8c145a1361c477c67e87f0e74a1c45faa50')
            self.assertEqual(receipt['npm_root_metadata_changes']['engines'], {'node': '>=26'})
            self.assertEqual(receipt['candidate_locked_package_records'], 30)
            self.assertIsNone(receipt['installed_bytes'])
            self.assertFalse(receipt['application_source_staged'])
            self.assertFalse((output / 'browser-test-kit/node_modules').exists())


# ═══ · nano btk node26 parser updates ════════════════════════════════════════════════════════════════════
# Additive Node26 parser profiles: exact inputs and fail-closed projections.

NANO_BTK_NODE26_PARSER_UPDATES_SPEC = importlib.util.spec_from_file_location('nano_parser_updates', ROOT / 'tools/nano_node.py')
nano_nano_btk_node26_parser_updates = importlib.util.module_from_spec(NANO_BTK_NODE26_PARSER_UPDATES_SPEC)
NANO_BTK_NODE26_PARSER_UPDATES_SPEC.loader.exec_module(nano_nano_btk_node26_parser_updates)

class ParserProfileTests(unittest.TestCase):
    def profiles(self):
        for label, feature in [('marked', nano_nano_btk_node26_parser_updates.BTK_NODE26_MARKED_FEATURE), ('parsers', nano_nano_btk_node26_parser_updates.BTK_NODE26_PARSERS_FEATURE)]:
            source = ROOT / 'tests/fixtures' / ('btk-node26-' + label)
            manifest = json.loads((source / 'package.json').read_bytes())
            lock = json.loads((source / 'package-lock.json').read_bytes())
            commit, blobs, selected, paths = nano_nano_btk_node26_parser_updates.BTK_NODE26_PROFILES[feature]
            yield label, feature, source, manifest, lock, commit, blobs, selected, paths

    @staticmethod
    def projected(lock, selected, paths):
        result = copy.deepcopy(lock)
        result['name'] = 'browser-test-kit'
        result['packages'] = {k: v for k, v in result['packages'].items() if not k or k in paths}
        result['packages']['']['devDependencies'] = selected
        return result

    def test_exact_snapshots_and_closures(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            with self.subTest(profile=label):
                self.assertRegex(commit, r'^[0-9a-f]{40}$')
                nano_nano_btk_node26_parser_updates.read_snapshot(source, blobs=blobs)
                nano_nano_btk_node26_parser_updates.validate_original(manifest, lock, feature=feature)
                self.assertEqual(len(paths), 30)
                self.assertEqual(manifest['engines'], {'node': '>=26'})
                self.assertEqual(lock['packages']['node_modules/marked']['version'], '18.1.2')
                self.assertEqual(lock['packages']['node_modules/linkedom']['version'], '0.18.14' if label == 'parsers' else '0.18.13')
                self.assertEqual(lock['packages']['node_modules/exceljs']['version'], '4.4.0')
                self.assertNotIn('node_modules/exceljs', paths)
                self.assertNotIn('node_modules/canvas', paths)
                self.assertEqual('node_modules/@acemir/cssom' in paths, label == 'parsers')
                self.assertEqual('node_modules/cssom' in paths, label == 'marked')
                projected = self.projected(lock, selected, paths)
                nano_nano_btk_node26_parser_updates.validate_projection(lock, projected, feature=feature)
                for path in paths:
                    self.assertEqual(projected['packages'][path], lock['packages'][path])

    def test_changed_inputs_and_wrong_profile_are_rejected(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp:
                changed = Path(tmp)
                for name in blobs:
                    (changed / name).write_bytes((source / name).read_bytes())
                with (changed / 'package.json').open('a') as stream:
                    stream.write(' ')
                with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                    nano_nano_btk_node26_parser_updates.read_snapshot(changed, blobs=blobs)
                with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                    nano_nano_btk_node26_parser_updates.read_snapshot(source, blobs=nano_nano_btk_node26_parser_updates.BTK_NODE26_SOURCE_BLOBS)

    def test_projection_rejects_mutated_retained_records(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            for mode in ('version', 'integrity', 'peer', 'engine', 'extra', 'missing'):
                projected = self.projected(lock, selected, paths)
                if mode == 'version': projected['packages']['node_modules/marked']['version'] = '18.1.1'
                if mode == 'integrity': projected['packages']['node_modules/marked']['integrity'] = 'sha512-wrong'
                if mode == 'peer': projected['packages']['node_modules/linkedom']['peerDependenciesMeta']['canvas']['optional'] = False
                if mode == 'engine': projected['packages']['']['engines'] = {'node': '>=24'}
                if mode == 'extra': projected['packages']['node_modules/canvas'] = {}
                if mode == 'missing': del projected['packages']['node_modules/marked']
                with self.subTest(profile=label, mode=mode), self.assertRaises(ValueError):
                    nano_nano_btk_node26_parser_updates.validate_projection(lock, projected, feature=feature)

    def test_original_rejects_wrong_engine_and_peer(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            for mode in ('engine', 'peer'):
                m, l = copy.deepcopy(manifest), copy.deepcopy(lock)
                if mode == 'engine': m['engines'] = {'node': '>=24'}
                if mode == 'peer': l['packages']['node_modules/linkedom']['peerDependencies']['canvas'] = '*'
                with self.subTest(profile=label, mode=mode), self.assertRaises(ValueError):
                    nano_nano_btk_node26_parser_updates.validate_original(m, l, feature=feature)

    def test_dependency_only_required_before_npm(self):
        for label, feature, source, *_ in self.profiles():
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp, patch.object(nano_nano_btk_node26_parser_updates.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'requires --dependencies-only'):
                    nano_nano_btk_node26_parser_updates.prepare(source, Path(tmp), feature=feature)
                run.assert_not_called()

    def test_prepare_receipts_do_not_claim_install_or_copy_source(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            def fake_npm(argv, **kwargs):
                if argv[1:] == ['--version']:
                    return subprocess.CompletedProcess(argv, 0, '11.9.0-test\n', '')
                self.assertEqual(argv[1:7], ['install', '--package-lock-only', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
                project = Path(kwargs['cwd'])
                self.assertEqual({p.name for p in project.iterdir()}, {'package.json', 'package-lock.json'})
                (project / 'package-lock.json').write_text(json.dumps(self.projected(lock, selected, paths)))
                return subprocess.CompletedProcess(argv, 0, '', '')
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp, patch.object(nano_nano_btk_node26_parser_updates.shutil, 'which', return_value=sys.executable), patch.object(nano_nano_btk_node26_parser_updates.subprocess, 'run', side_effect=fake_npm):
                output = nano_nano_btk_node26_parser_updates.prepare(source, Path(tmp), feature=feature, dependencies_only=True)
                receipt = json.loads((output / 'receipt.json').read_text())
                self.assertEqual(receipt['source_commit'], commit)
                self.assertEqual(receipt['source_git_blobs'], blobs)
                self.assertEqual(receipt['candidate_locked_package_records'], 30)
                self.assertEqual(receipt['npm_root_metadata_changes']['engines'], {'node': '>=26'})
                self.assertIsNone(receipt['installed_package_count'])
                self.assertFalse(receipt['application_source_staged'])
                self.assertFalse((output / 'browser-test-kit/node_modules').exists())


if __name__ == "__main__":
    unittest.main()
