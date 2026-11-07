"""Offline checks for descriptive screening data; never install or fetch sources."""
import json, unittest
from pathlib import Path, PurePosixPath


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
                commit_key = "source_commit" if entry["repository"] == "myon-bioinformatics/myon-bioinformatics" else "commit"
                self.assertRegex(entry[commit_key], r"^[0-9a-f]{40}$")
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
                                         entry["repository"], entry.get("source_commit", entry.get("commit")), source["path"]))

    def test_delivery_matrix_covers_every_screened_repository_once(self):
        guide = (ROOT / "docs/nano-environments.md").read_text()
        table = guide.split("| Repository |", 1)[1].split("### Coverage", 1)[0]
        rows = [line.split("|")[1:-1] for line in table.splitlines()
                if line.startswith("| ")]
        self.assertTrue(all(len(row) == 4 for row in rows))
        self.assertTrue(all(row[3].strip() for row in rows))
        names = [row[0].strip() for row in rows]
        expected = [entry["repository"].split("/", 1)[1]
                    for entry in self.snapshot["repositories"]]
        self.assertCountEqual(names, expected)
        self.assertEqual(len(names), len(set(names)))

    def test_parent_inventory_provenance_is_not_an_active_consumer_pin(self):
        from tools.parent_pin_check import PARENT_REPOSITORY, evaluate, scan_text_files
        from unittest.mock import patch

        records = [entry for entry in self.snapshot["repositories"] + self.snapshot["reuse"]
                   if entry["repository"] == PARENT_REPOSITORY]
        self.assertEqual(len(records), 2)
        historical = "8d0bfcb7aaf955c25f2f8612b21a5321b44d09c4"
        for entry in records:
            self.assertNotIn("commit", entry)
            self.assertNotIn("sha", entry)
            self.assertEqual(entry["source_commit"], historical)
        inventory = {"docs/stdlib-shrink-queue.json": json.dumps(self.catalog)}
        self.assertEqual(scan_text_files(inventory), [])

        # Provenance is not a blanket exclusion: real consumer locks/workflows
        # must still be detected, and stale pins must still fail strict mode.
        controls = {
            "vendor.lock.json": json.dumps({"files": [{
                "repository": PARENT_REPOSITORY, "commit": historical,
                "destination": "vendor/tool.py",
            }]}),
            ".github/workflows/control.yml":
                f"uses: {PARENT_REPOSITORY}/.github/workflows/reusable.yml@{historical}\n",
        }
        pins = scan_text_files(inventory | controls)
        self.assertEqual({pin["kind"] for pin in pins}, {"json-commit", "workflow-uses", "text-reference"})
        self.assertEqual({pin["file"] for pin in pins}, set(controls))
        with patch("tools.parent_pin_check.pin_behind_main", return_value=True):
            report = evaluate(pins, parent_repository=PARENT_REPOSITORY,
                              main_sha="f" * 40, token=None, enforce=True)
        self.assertFalse(report["ok"])
        self.assertTrue(report["stale"])

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
