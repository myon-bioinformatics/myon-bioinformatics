"""Catalog policy for py_launch.cjs until post-merge pin lands on default branch."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_py_launch_not_catalogued_until_merge_commit_pin():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    sources = {tool["source"] for tool in catalog["tools"]}
    assert "py_launch.cjs" not in sources


def test_py_launch_source_present_in_repository():
    assert (ROOT / "py_launch.cjs").is_file()
