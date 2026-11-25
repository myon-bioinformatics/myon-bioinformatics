"""Artifact gate regression tests using synthetic tar archives."""
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_pages_artifact', ROOT / 'scripts/verify_pages_artifact.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def archive(tmp_path, files):
    path = tmp_path / 'artifact.tar'
    with tarfile.open(path, 'w:gz') as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def test_pages_artifact_accepts_local_links_and_fragments(tmp_path):
    path = archive(tmp_path, {
        './index.html': b'<a data-ui-back href="guide/">Guide</a><a href="#weekly">Weekly</a><section id="weekly"></section><link href="style.css">',
        './guide/index.html': b'<img src="../movie.gif"><script src="../script.js"></script>',
        './style.css': b'body{}', './script.js': b'//code',
        './movie.gif': b'GIF89a\x01\x00\x01\x00\x00\x00\x00',
    })
    assert validator.verify(path) == []


def test_pages_artifact_rejects_missing_local_link(tmp_path):
    errors = validator.verify(archive(tmp_path, {'index.html': b'<a href="tools/ironmate/">Old</a>'}))
    assert any('missing local href' in error for error in errors)


def test_pages_artifact_rejects_missing_hash_target(tmp_path):
    errors = validator.verify(archive(tmp_path, {'index.html': b'<a href="#absent">Missing</a>'}))
    assert any('missing fragment' in error for error in errors)


def test_pages_artifact_rejects_duplicate_back_nav_and_invalid_gif(tmp_path):
    errors = validator.verify(archive(tmp_path, {
        'index.html': b'<a data-ui-back href="#">Back</a><a data-ui-back href="#">Back again</a>',
        'broken.gif': b'not a GIF',
    }))
    assert any('duplicated common back navigation' in error for error in errors)
    assert any('invalid GIF header' in error for error in errors)


def test_pages_artifact_rejects_path_escape_and_missing_html(tmp_path):
    errors = validator.verify(archive(tmp_path, {'../index.html': b'hi', 'image.png': b'png'}))
    assert any('unsafe or duplicate archive path' in error for error in errors)
    assert any('no HTML documents' in error for error in errors)


def test_pages_artifact_does_not_claim_external_links_are_checked(tmp_path):
    path = archive(tmp_path, {'index.html': b'<a href="https://example.org/foo">External</a><img src="/tools/image-studio/logo.png">'})
    assert validator.verify(path) == []


def test_pages_artifact_rejects_bad_archive(tmp_path):
    path = tmp_path / 'artifact.tar'
    path.write_bytes(b'not a tar')
    assert any('invalid Pages artifact' in error for error in validator.verify(path))
