import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import vendor_sync as sync

OLD = "1" * 40
NEW = "2" * 40


def entry(data=b"old\n", source="scripts/adapter.py", destination="vendor/adapter.py"):
    return dict(repository="myon-bioinformatics/xprobe", ref="main", commit=OLD,
                source=source, destination=destination, blob_sha=sync.git_blob(data),
                sha256=hashlib.sha256(data).hexdigest())


def lock(tmp_path, entries=None):
    value = {"schema": sync.SCHEMA, "files": entries or [entry()]}
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path, value


def test_materialize_and_offline_check(tmp_path):
    path, value = lock(tmp_path)
    urls = []

    def get(url):
        urls.append(url)
        return b"old\n"

    result = sync.synchronize(path.name, tmp_path, "materialize", get=get)
    assert result["changed_paths"] == ["vendor/adapter.py"]
    assert urls == [f"https://raw.githubusercontent.com/myon-bioinformatics/xprobe/{OLD}/scripts/adapter.py"]
    assert (tmp_path / "vendor/adapter.py").read_bytes() == b"old\n"
    assert json.loads(path.read_text(encoding="utf-8")) == value
    no_network = lambda url: pytest.fail("network used for valid local files")
    assert not sync.synchronize(path.name, tmp_path, "check", get=no_network)["changed_paths"]
    assert not sync.synchronize(path.name, tmp_path, "materialize", get=no_network)["changed_paths"]


@pytest.mark.parametrize("field", ["blob_sha", "sha256"])
def test_bad_digest_leaves_all_files_and_lock_unchanged(tmp_path, field):
    entries = [entry(source="one.py", destination="vendor/one.py"),
               entry(source="two.py", destination="vendor/two.py")]
    entries[1][field] = "0" * (40 if field == "blob_sha" else 64)
    path, _ = lock(tmp_path, entries)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    assert not (tmp_path / "vendor").exists()
    assert path.read_bytes() == before


def upstream(data, calls):
    def get(url):
        calls.append(url)
        if "/commits?" in url:
            return json.dumps([{"sha": NEW}]).encode()
        if "/contents/" in url:
            return json.dumps({"type": "file", "sha": sync.git_blob(data)}).encode()
        assert "/" + NEW + "/" in url
        return data
    return get


def test_update_refreshes_pin_and_bytes_without_executing_them(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    payload = b"raise RuntimeError('never import candidate')\n"
    calls = []
    result = sync.synchronize(path.name, tmp_path, "update", get=upstream(payload, calls))
    assert result["changed_paths"] == ["vendor/adapter.py", path.name]
    current = json.loads(path.read_text(encoding="utf-8"))["files"][0]
    assert current["commit"] == NEW
    assert current["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / current["destination"]).read_bytes() == payload
    assert len(calls) == 3
    sync.synchronize(path.name, tmp_path, "check")


def test_update_no_churn_for_unrelated_upstream_commit(tmp_path):
    path, old = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    before = path.read_bytes()
    assert not sync.synchronize(path.name, tmp_path, "update", get=upstream(b"old\n", []))["changed_paths"]
    assert path.read_bytes() == before
    assert json.loads(before) == old


def test_group_resolves_one_commit_and_includes_license(tmp_path):
    path, _ = lock(tmp_path, [entry(), entry(source="LICENSE", destination="vendor/LICENSE")])
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    calls = []
    sync.synchronize(path.name, tmp_path, "update", get=upstream(b"new\n", calls))
    assert sum("/commits?" in url for url in calls) == 1
    assert {i["commit"] for i in json.loads(path.read_text(encoding="utf-8"))["files"]} == {NEW}
    assert (tmp_path / "vendor/LICENSE").read_bytes() == b"new\n"


def test_upstream_blob_mismatch_does_not_write(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    before = path.read_bytes()
    get = upstream(b"good\n", [])
    def corrupt(url):
        return b"bad\n" if url.startswith("https://raw.") else get(url)
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "update", get=corrupt)
    assert path.read_bytes() == before
    assert (tmp_path / "vendor/adapter.py").read_bytes() == b"old\n"


@pytest.mark.parametrize("destination", ["/outside", "../outside", "vendor/../outside", "C:/outside", "a\\b", ".git/config"])
def test_unsafe_destinations_rejected_without_network(tmp_path, destination):
    path, _ = lock(tmp_path, [entry(destination=destination)])
    with pytest.raises(ValueError):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: pytest.fail("network"))


def test_symlink_and_manifest_collision(tmp_path):
    path, _ = lock(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "vendor").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        sync.synchronize(path.name, tmp_path, "materialize")
    (tmp_path / "vendor").unlink()
    lock(tmp_path, [entry(destination=path.name)])
    with pytest.raises(ValueError, match="collides"):
        sync.synchronize(path.name, tmp_path, "update")


def test_duplicate_overlap_and_short_sha():
    item = entry()
    for entries in ([item, copy.deepcopy(item)], [item, entry(destination="vendor/adapter.py/nested")],
                    [{**item, "commit": "1234567"}]):
        with pytest.raises(ValueError):
            sync.validate({"schema": sync.SCHEMA, "files": entries})


def test_cli_help_has_no_side_effects_and_missing_file_is_red(tmp_path):
    script = Path(sync.__file__).resolve()
    help_result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "{check,materialize,update}" in help_result.stdout
    assert not list(tmp_path.iterdir())
    result = subprocess.run([sys.executable, str(script), "check"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stderr.startswith("vendor-sync:")


def test_update_rejects_local_edits_before_network(tmp_path):
    path, _ = lock(tmp_path)
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    file = tmp_path / "vendor/adapter.py"
    file.write_bytes(b"local edit\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="digest mismatch"):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: pytest.fail("network"))
    assert file.read_bytes() == b"local edit\n"
    assert path.read_bytes() == before


def test_atomic_preserves_executable_mode_and_defaults_readable(tmp_path):
    existing = tmp_path / "existing.py"
    existing.write_bytes(b"old")
    existing.chmod(0o755)
    sync._atomic(existing, b"new")
    assert existing.stat().st_mode & 0o777 == 0o755
    fresh = tmp_path / "fresh.py"
    sync._atomic(fresh, b"new")
    assert fresh.stat().st_mode & 0o777 == 0o644


def test_casefold_manifest_collision(tmp_path):
    path, _ = lock(tmp_path, [entry(destination="VENDOR.LOCK.JSON")])
    with pytest.raises(ValueError, match="collides"):
        sync.synchronize(path.name, tmp_path, "materialize")


def test_slash_ref_query_and_empty_commit_list(tmp_path):
    item = entry()
    item["ref"] = "feature/adapter"
    path, _ = lock(tmp_path, [item])
    sync.synchronize(path.name, tmp_path, "materialize", get=lambda url: b"old\n")
    calls = []
    sync.synchronize(path.name, tmp_path, "update", get=upstream(b"old\n", calls))
    assert calls[0] == "https://api.github.com/repos/myon-bioinformatics/xprobe/commits?sha=feature%2Fadapter&per_page=1"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="no commits"):
        sync.synchronize(path.name, tmp_path, "update", get=lambda url: b"[]")
    assert path.read_bytes() == before
