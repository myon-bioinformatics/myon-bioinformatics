import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import vendor_consumers as consumers
import vendor_sync as sync


def registry():
    return {
        "schema": consumers.SCHEMA,
        "consumers": [
            {
                "repository": "owner/app",
                "state": "locked",
                "lock": "vendor.lock.json",
                "entries": [
                    {
                        "repository": "owner/tool",
                        "source": "tool.py",
                        "destination": "vendor/tool.py",
                    },
                    {
                        "repository": "owner/tool",
                        "source": "LICENSE",
                        "destination": "vendor/tool-LICENSE",
                    },
                ],
            },
            {
                "repository": "owner/legacy",
                "state": "legacy",
                "lock": None,
                "reason": "legacy_provenance_without_vendor_lock",
                "legacy_provenance": ["vendor/tool.provenance.json"],
                "entries": [
                    {
                        "repository": "owner/tool",
                        "source": "tool.py",
                        "destination": "vendor/tool.py",
                    }
                ],
            },
        ],
    }


def lock():
    data = b"tool\n"
    license_data = b"license\n"
    return {
        "schema": sync.SCHEMA,
        "files": [
            {
                "repository": "owner/tool",
                "ref": "refs/heads/main",
                "commit": "1" * 40,
                "source": "tool.py",
                "destination": "vendor/tool.py",
                "blob_sha": sync.git_blob(data),
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
            },
            {
                "repository": "owner/tool",
                "ref": "refs/heads/main",
                "commit": "1" * 40,
                "source": "LICENSE",
                "destination": "vendor/tool-LICENSE",
                "blob_sha": sync.git_blob(license_data),
                "sha256": __import__("hashlib").sha256(license_data).hexdigest(),
            },
        ],
    }


def test_validate_normalizes_without_mutating():
    value = registry()
    before = copy.deepcopy(value)
    result = consumers.validate(value)
    assert result == before
    assert value == before
    assert consumers.get_consumer(value, "OWNER/App")["repository"] == "owner/app"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(schema="wrong"),
        lambda doc: doc["consumers"].append(copy.deepcopy(doc["consumers"][0])),
        lambda doc: doc["consumers"][0].update(state="pending"),
        lambda doc: doc["consumers"][0].update(lock=None),
        lambda doc: doc["consumers"][1].update(lock="vendor.lock.json"),
        lambda doc: doc["consumers"][1].pop("reason"),
        lambda doc: doc["consumers"][1].update(reason="not a code"),
        lambda doc: doc["consumers"][0]["entries"][0].update(destination="../escape.py"),
        lambda doc: doc["consumers"][0]["entries"][0].update(repository="owner/app"),
    ],
)
def test_invalid_registry(mutate):
    doc = registry()
    mutate(doc)
    with pytest.raises(ValueError):
        consumers.validate(doc)


def test_duplicate_destination_is_case_insensitive():
    doc = registry()
    doc["consumers"][0]["entries"].append(
        {
            "repository": "owner/other",
            "source": "other.py",
            "destination": "VENDOR/TOOL.PY",
        }
    )
    with pytest.raises(ValueError, match="destination"):
        consumers.validate(doc)


def test_compare_reports_topology_only():
    doc = registry()
    value = lock()
    assert consumers.compare(doc, value, "OWNER/app") == {
        "schema": "vendor-consumer-comparison/1",
        "consumer": "owner/app",
        "lock": "vendor.lock.json",
        "matches": True,
        "missing": [],
        "unexpected": [],
    }

    value["files"].pop()
    result = consumers.compare(doc, value, "owner/app")
    assert not result["matches"]
    assert result["missing"] == [
        {
            "repository": "owner/tool",
            "source": "LICENSE",
            "destination": "vendor/tool-LICENSE",
        }
    ]

    extra = dict(value["files"][0])
    extra.update(
        repository="owner/other",
        source="extra.py",
        destination="vendor/extra.py",
    )
    value["files"].append(extra)
    result = consumers.compare(doc, value, "owner/app")
    assert result["unexpected"] == [
        {
            "repository": "owner/other",
            "source": "extra.py",
            "destination": "vendor/extra.py",
        }
    ]


def test_legacy_consumer_has_no_lock_comparison():
    with pytest.raises(ValueError, match="legacy"):
        consumers.compare(registry(), lock(), "owner/legacy")


def test_checked_in_registry_and_parent_lock():
    root = Path(__file__).resolve().parents[1]
    doc = consumers.validate(
        json.loads((root / "vendor-consumers.json").read_text(encoding="utf-8"))
    )
    by_repo = {row["repository"]: row for row in doc["consumers"]}
    expected_locked = {
        "myon-bioinformatics/myon-bioinformatics",
        "myon-bioinformatics/yourself",
        "myon-bioinformatics/nvd_nist_known_vulns",
        "myon-bioinformatics/convert_img_fmt_to_webp-CUI-",
        "myon-bioinformatics/ascii_artist",
        "myon-bioinformatics/markdown",
        "myon-bioinformatics/mcp-toolcall-lab",
        "myon-bioinformatics/Ironmate",
        "myon-bioinformatics/browser-test-kit",
        "myon-bioinformatics/web-ui",
        "myon-bioinformatics/flutter_navigation_basic",
    }
    assert {repo for repo, row in by_repo.items() if row["state"] == "locked"} == expected_locked
    assert "myon-bioinformatics/xprobe" not in by_repo
    legacy = by_repo["myon-bioinformatics/search_seq_including_spaces"]
    assert legacy["state"] == "legacy"
    assert legacy["lock"] is None

    parent_lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    comparison = consumers.compare(
        doc, parent_lock, "myon-bioinformatics/myon-bioinformatics"
    )
    assert comparison["matches"]


def test_cli_validate_compare_and_errors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry_path = tmp_path / "registry.json"
    lock_path = tmp_path / "vendor.lock.json"
    registry_path.write_text(json.dumps(registry()), encoding="utf-8")
    lock_path.write_text(json.dumps(lock()), encoding="utf-8")

    assert consumers.main(["validate", "--registry", str(registry_path)]) == 0
    assert consumers.main(
        [
            "compare",
            "--registry",
            str(registry_path),
            "--consumer",
            "owner/app",
            "--lock",
            str(lock_path),
        ]
    ) == 0
    assert consumers.main(["compare", "--registry", str(registry_path)]) == 2
    assert consumers.main(["validate", "--registry", str(tmp_path / "missing")]) == 2

    result = subprocess.run(
        [sys.executable, str(root / "vendor_consumers.py"), "--help"],
        capture_output=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("drift", ["missing", "unexpected"])
def test_compare_cli_fails_closed_with_json_evidence(tmp_path, drift):
    registry_path = tmp_path / "registry.json"
    lock_path = tmp_path / "vendor.lock.json"
    registry_path.write_text(json.dumps(registry()), encoding="utf-8")
    value = lock()
    if drift == "missing":
        value["files"].pop()
    else:
        value["files"].append(dict(value["files"][0], destination="vendor/extra.py"))
    lock_path.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-S", str(Path(consumers.__file__)), "compare",
         "--registry", str(registry_path), "--consumer", "owner/app",
         "--lock", str(lock_path)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1
    evidence = json.loads(result.stdout)
    assert evidence["matches"] is False
    assert len(evidence[drift]) == 1
    assert not result.stderr
