"""js_adopt: pinned raw GitHub files, sha256/SRI lock, license gate, script tags (offline)."""
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import js_adopt as ja  # noqa: E402

BODY = b"/*! lib */window.lib=1;"


def fake_fetch(seen):
    def fetch(url):
        seen.append(url)
        return BODY
    return fetch


def adopt(tmp_path, **kw):
    lock = ja.load_lock(tmp_path / "js.lock.json")
    seen = []
    args = dict(license="MIT", fetch=fake_fetch(seen))
    args.update(kw)
    entry = ja.add(lock, tmp_path, "cure53/DOMPurify", "3.4.17", "dist/purify.min.js", "vendor/js", **args)
    ja.save_lock(tmp_path / "js.lock.json", lock)
    return lock, entry, seen


def test_add_records_pinned_url_digests_and_writes_the_file(tmp_path):
    lock, entry, seen = adopt(tmp_path)
    assert seen == ["https://raw.githubusercontent.com/cure53/DOMPurify/3.4.17/dist/purify.min.js"]
    assert (tmp_path / "vendor/js/purify.min.js").read_bytes() == BODY
    assert entry["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert entry["sri"] == "sha384-" + base64.b64encode(hashlib.sha384(BODY).digest()).decode()
    assert (entry["name"], entry["bytes"]) == ("purify", len(BODY))
    assert json.loads((tmp_path / "js.lock.json").read_text())["schema"] == "js-adopt/1"


def test_re_adding_replaces_instead_of_duplicating(tmp_path):
    adopt(tmp_path)
    lock, _, _ = adopt(tmp_path)
    assert len(lock["files"]) == 1


@pytest.mark.parametrize(("expression", "ok"), [
    ("MIT", True), ("MPL-2.0 OR Apache-2.0", True), ("(MIT OR GPL-3.0)", True),
    ("GPL-3.0", False), ("MIT AND GPL-3.0", False), ("LGPL-3.0-or-later", False), ("", False),
])
def test_license_expressions(expression, ok):
    assert ja.license_ok(expression) is ok


def test_disallowed_license_is_refused_before_download(tmp_path):
    seen = []
    with pytest.raises(ja.LicenseNotAllowed):
        ja.add(ja.load_lock(tmp_path / "x.json"), tmp_path, "a/b", "v1", "x.js", "v", license="GPL-3.0",
               fetch=fake_fetch(seen))
    assert seen == [] and not (tmp_path / "v").exists()


@pytest.mark.parametrize(("repo", "ref", "path", "dest"), [
    ("nope", "v1", "x.js", "v"), ("a/b", "-x", "x.js", "v"), ("a/b", "v1", "../x.js", "v"),
    ("a/b", "v1", "x.js", "/abs"), ("a/b", "v1", "x.js", "a/../b"), ("a/b", "v 1", "x.js", "v"),
])
def test_invalid_input_writes_nothing(tmp_path, repo, ref, path, dest):
    with pytest.raises(ValueError):
        ja.add(ja.load_lock(tmp_path / "x.json"), tmp_path, repo, ref, path, dest, license="MIT",
               fetch=fake_fetch([]))
    assert list(tmp_path.iterdir()) == []


def test_check_detects_local_edit_missing_file_and_moved_ref(tmp_path):
    lock, _, _ = adopt(tmp_path)
    assert ja.check(lock, tmp_path) == []
    assert ja.check(lock, tmp_path, remote=True, fetch=lambda url: b"other") == [
        "purify: 3.4.17 now serves different bytes (moved tag?)"]
    (tmp_path / "vendor/js/purify.min.js").write_bytes(BODY + b" ")
    assert ja.check(lock, tmp_path) == ["purify: local bytes differ from the lock"]
    (tmp_path / "vendor/js/purify.min.js").unlink()
    assert ja.check(lock, tmp_path) == ["purify: missing vendor/js/purify.min.js"]


def test_add_pins_line_endings_and_check_reports_a_missing_rule(tmp_path):
    """CRLF_BREAKS_SRI: Windows autocrlf rewrote adopted JS and every engine refused it."""
    (tmp_path / ".gitattributes").write_text("other.py -text")  # no trailing newline
    lock, _, _ = adopt(tmp_path)
    assert (tmp_path / ".gitattributes").read_text() == "other.py -text\nvendor/js/purify.min.js -text\n"
    adopt(tmp_path)
    assert (tmp_path / ".gitattributes").read_text().count("purify") == 1  # idempotent
    (tmp_path / ".gitattributes").write_text("vendor/js/** -text\n")
    assert ja.check(lock, tmp_path) == []
    (tmp_path / ".gitattributes").write_text("vendor/js/** text\n")
    assert ja.check(lock, tmp_path) == [
        "purify: .gitattributes lacks 'vendor/js/purify.min.js -text' (CRLF would break SRI)"]


def test_tags_and_oneliners_carry_the_same_integrity(tmp_path):
    _, entry, _ = adopt(tmp_path)
    assert ja.script_tag(entry, base="../") == (
        f'<script src="../vendor/js/purify.min.js" integrity="{entry["sri"]}" crossorigin="anonymous"></script>')
    assert 'src="https://cdn.jsdelivr.net/gh/cure53/DOMPurify@3.4.17/dist/purify.min.js"' in ja.script_tag(entry, cdn=True)
    curl, fetch, py = ja.oneliners(entry)
    assert entry["sha256"] in curl and "--max-time" in curl and "sha256" in curl
    assert entry["sri"] in fetch and "AbortController" in fetch
    assert "threading.Timer" in py and entry["sha256"] in py


def test_cli_runs_under_python_s(tmp_path):
    adopt(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, "-S", str(ROOT / "js_adopt.py"), "--root", str(tmp_path), *a],
                                    capture_output=True, text=True, timeout=60)
    done = run("check")
    assert done.returncode == 0 and "1 adopted, 0 problem(s)" in done.stdout
    assert run("tag", "purify").stdout.startswith('<script src="vendor/js/purify.min.js"')
    assert run("tag", "missing").returncode == 2
    (tmp_path / "vendor/js/purify.min.js").write_text("tampered")
    assert run("check").returncode == 1


def npm_release(files, *, name="js-yaml", version="5.5.0", license="MIT", top="package", tamper=False):
    """(fetch, seen) serving registry metadata and a gzip tarball built from ``files``."""
    import gzip
    import io
    import tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, data in files.items():
            info = tarfile.TarInfo(f"{top}/{path}")
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "../../etc/passwd"
                archive.addfile(info)
                continue
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    tarball = gzip.compress(buffer.getvalue())
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    tar_url = f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"
    meta = {"name": name, "version": version, "license": license, "dist": {"tarball": tar_url, "integrity": integrity}}
    seen = []

    def fetch(url):
        seen.append(url)
        if url == tar_url:
            return tarball + (b"x" if tamper else b"")
        return json.dumps(meta).encode()
    return fetch, seen


def test_add_npm_verifies_the_tarball_and_extracts_one_file(tmp_path):
    fetch, seen = npm_release({"dist/browser/js-yaml.umd.min.js": BODY, "README.md": b"r"})
    lock = ja.load_lock(tmp_path / "js.lock.json")
    entry = ja.add_npm(lock, tmp_path, "js-yaml", "5.5.0", "dist/browser/js-yaml.umd.min.js", "vendor/js",
                       license="MIT", fetch=fetch)
    assert seen == ["https://registry.npmjs.org/js-yaml/5.5.0", "https://registry.npmjs.org/js-yaml/-/js-yaml-5.5.0.tgz"]
    assert (tmp_path / "vendor/js/js-yaml.umd.min.js").read_bytes() == BODY
    assert (entry["name"], entry["source"], entry["package"], entry["version"]) == ("js-yaml", "npm", "js-yaml", "5.5.0")
    assert entry["sha256"] == hashlib.sha256(BODY).hexdigest() and entry["tarball_integrity"].startswith("sha512-")
    assert ja.cdn_url(entry) == "https://cdn.jsdelivr.net/npm/js-yaml@5.5.0/dist/browser/js-yaml.umd.min.js"
    assert ja.check(lock, tmp_path, remote=True, fetch=fetch) == []
    assert "vendor/js/js-yaml.umd.min.js -text" in (tmp_path / ".gitattributes").read_text()
    assert all(ja.cdn_url(entry) in line for line in ja.oneliners(entry))  # the file, not the tarball


def test_add_npm_names_scoped_packages_and_renames_the_file(tmp_path):
    fetch, seen = npm_release({"dist/index.cjs": BODY}, name="@scope/smol", license="BSD-3-Clause")
    entry = ja.add_npm(ja.load_lock(tmp_path / "x.json"), tmp_path, "@scope/smol", "5.5.0", "dist/index.cjs",
                       "vendor/js", license="BSD-3-Clause", file_name="smol.cjs", fetch=fetch)
    assert seen[0] == "https://registry.npmjs.org/@scope%2Fsmol/5.5.0"
    assert (entry["name"], entry["destination"]) == ("smol", "vendor/js/smol.cjs")


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"version": "^5.5.0"}, "exact version"), ({"version": "latest"}, "exact version"),
    ({"package": "../evil"}, "npm package"), ({"path": "../x.js"}, "invalid path"),
    ({"file_name": "a/b.js"}, "file name"), ({"license": "BSD-3-Clause"}, "differs"),
])
def test_add_npm_refuses_bad_input_and_license_mismatch(tmp_path, kwargs, message):
    fetch, _ = npm_release({"dist/a.js": BODY})
    args = dict(package="js-yaml", version="5.5.0", path="dist/a.js", license="MIT")
    args.update(kwargs)
    with pytest.raises(ValueError, match=message):
        ja.add_npm(ja.load_lock(tmp_path / "x.json"), tmp_path, args.pop("package"), args.pop("version"),
                   args.pop("path"), "vendor/js", fetch=fetch, **args)


@pytest.mark.parametrize(("files", "options", "message"), [
    ({"dist/a.js": BODY}, {"tamper": True}, "registry integrity"),
    ({"dist/b.js": BODY}, {}, "no regular file"),
    ({"dist/a.js": None}, {}, "no regular file"),  # a symlink is never followed
])
def test_add_npm_fails_closed_before_writing(tmp_path, files, options, message):
    fetch, _ = npm_release(files, **options)
    with pytest.raises(ValueError, match=message):
        ja.add_npm(ja.load_lock(tmp_path / "x.json"), tmp_path, "js-yaml", "5.5.0", "dist/a.js", "vendor/js",
                   license="MIT", fetch=fetch)
    assert not (tmp_path / "vendor").exists()


def test_check_remote_reports_a_changed_npm_release(tmp_path):
    fetch, _ = npm_release({"dist/a.js": BODY})
    lock = ja.load_lock(tmp_path / "x.json")
    ja.add_npm(lock, tmp_path, "js-yaml", "5.5.0", "dist/a.js", "vendor/js", license="MIT", fetch=fetch)
    changed, _ = npm_release({"dist/a.js": BODY + b"//"})
    assert ja.check(lock, tmp_path, remote=True, fetch=changed) == ["js-yaml: js-yaml@5.5.0 now serves different bytes"]
    tampered, _ = npm_release({"dist/a.js": BODY}, tamper=True)
    assert "registry integrity" in ja.check(lock, tmp_path, remote=True, fetch=tampered)[0]
