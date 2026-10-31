"""Offline checks for descriptive screening data; never install or fetch sources."""
import json
from pathlib import Path, PurePosixPath
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NanoInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "docs/stdlib-shrink-queue.json").read_text())
        cls.snapshot = cls.catalog["current_snapshot"]

    def test_historical_contract_is_retained(self):
        self.assertEqual(self.catalog["schema"], "stdlib-shrink-queue/1")
        self.assertEqual(self.catalog["surveyed_at"], "2026-10-20")
        self.assertEqual(len(self.catalog["queue"]), 4)
        self.assertEqual(self.snapshot["schema"], "nano-screening/1")
        self.assertEqual(self.snapshot["status"], "inventory_only")

    def test_public_snapshot_has_unique_pinned_repositories(self):
        entries = self.snapshot["repositories"]
        self.assertEqual(len(entries), self.snapshot["scope"]["screened_repositories"])
        self.assertEqual(len(entries), 24)
        self.assertEqual(len({r["repository"] for r in entries}), len(entries))
        for entry in entries:
            with self.subTest(repository=entry["repository"]):
                self.assertRegex(entry["repository"], r"^myon-bioinformatics/[A-Za-z0-9_.-]+$")
                self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
                self.assertEqual(entry["visibility"], "public")
                self.assertIs(entry["private"], False)
                self.assertIs(entry["archived"], False)
                self.assertIn(entry["classification"], {
                    "already_minimal", "optional_features", "production_dependencies", "other_runtime"
                })
                for key in ("summary", "minimum_boundary"):
                    self.assertIsInstance(entry[key], str)
                    self.assertTrue(entry[key].strip())
                for role in ("production", "optional", "development"):
                    self.assertIsInstance(entry[role], list)
                    for feature in entry[role]:
                        self.assertIsInstance(feature, str)
                        self.assertTrue(feature.strip())

    def test_sources_are_pinned_metadata_not_mutable_commands(self):
        for entry in self.snapshot["repositories"] + self.snapshot["reuse"]:
            self.assertTrue(entry["evidence"])
            for source in entry["evidence"]:
                with self.subTest(repository=entry["repository"], path=source["path"]):
                    path = PurePosixPath(source["path"])
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertNotIn("\\", source["path"])
                    self.assertRegex(source["blob_sha"], r"^[0-9a-f]{40}$")
                    self.assertEqual(source["url"],
                                     "https://github.com/{}/blob/{}/{}".format(
                                         entry["repository"], entry["commit"], source["path"]))

    def test_measurements_and_execution_are_not_claimed(self):
        measurements = self.snapshot["measurements"]
        self.assertEqual(measurements["status"], "not_measured")
        self.assertIs(self.snapshot["executed_workloads"], False)
        self.assertIs(self.snapshot["installations_performed"], False)
        self.assertEqual(set(measurements["before"]), set(measurements["after"]))
        for side in ("before", "after"):
            self.assertTrue(measurements[side])
            self.assertTrue(all(value is None for value in measurements[side].values()))
        self.assertEqual(self.snapshot["changes"], {
            "runtime_files_deleted": 0, "runtime_lines_deleted": 0,
            "runtime_functions_added": 0, "tests_removed": 0,
            "dependencies_changed": 0, "child_repositories_changed": 0
        })

    def test_existing_runtime_profiles_remain_planning_only(self):
        profiles = json.loads((ROOT / "runtime-profiles.json").read_text())
        self.assertEqual(profiles["schema"], "nano-profiles/1")
        for profile in profiles["profiles"].values():
            self.assertTrue(all(value is None for value in profile["evidence"].values()))
        survey = (ROOT / "docs/stdlib-shrink-survey.md").read_text()
        self.assertIn("Historical", survey)
        self.assertIn("current_snapshot", survey)
        self.assertIn("nano-environments.md", survey)


if __name__ == "__main__":
    unittest.main()
