"""Keep the Aoi and Flutter source/LICENSE topology together across integration."""
import copy
import json
from pathlib import Path

import pytest

import vendor_consumers
import vendor_catalog
import vendor_sync

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("consumer,fixture,repository,source,destination,license_path,commit", [
    ("Aoi", "aoi_vendor_lock_85243d6d.json", "web-ui", "web_ui.py", "vendor/web_ui.py",
     "vendor/web-ui-LICENSE", "573b4ca07a916c4a44695dbd5739da36b902d8f2"),
    ("flutter_navigation_basic", "flutter_vendor_lock_f73c72eb.json", "myon-bioinformatics",
     "gh_ops.py", "python/vendor/gh_ops.py", "python/vendor/myon-bioinformatics-LICENSE",
     "09831b992dc91d83b15c44861a788e03b1e654c4"),
])
def test_adopted_sources_keep_their_license_and_exact_pin(
        consumer, fixture, repository, source, destination, license_path, commit):
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    lock = json.loads((ROOT / "tests/fixtures" / fixture).read_text())
    name = "myon-bioinformatics/" + consumer
    assert vendor_consumers.compare(registry, lock, name)["matches"]
    rows = {row["destination"]: row for row in lock["files"]}
    for path, expected_source in ((destination, source), (license_path, "LICENSE")):
        row = rows[path]
        assert row["repository"] == "myon-bioinformatics/" + repository
        assert row["source"] == expected_source
        assert row["ref"] == row["commit"] == commit
    without_license = copy.deepcopy(lock)
    without_license["files"] = [r for r in lock["files"] if r["destination"] != license_path]
    assert not vendor_consumers.compare(registry, without_license, name)["matches"]


def test_explicit_source_validation_keeps_network_pin_verification(monkeypatch):
    document = {"schema": "vendor-catalog/1", "tools": [{
        "repository": "owner/tool", "source": "tool.py", "commit": "a" * 40,
        "reason": "reviewed fixture",
    }]}
    checked = []
    monkeypatch.setattr(
        vendor_sync,
        "public_commit_exists",
        lambda repo, commit, **kwargs: checked.append((repo, commit, kwargs.get("entry_label"))),
    )
    vendor_catalog.validate(document, verify_pins=True, allow_legacy_implicit_source=False)
    assert checked == [("owner/tool", "a" * 40, "tool.py@" + ("a" * 40)[:7])]
    document["tools"][0].pop("source")
    with pytest.raises(ValueError, match="source required"):
        vendor_catalog.validate(document, verify_pins=True)
    assert len(checked) == 1
