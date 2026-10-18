"""Execute the generated examples against bounded, hostile loopback responses."""
import hashlib
import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest

import js_adopt as ja


@contextmanager
def server(body, mode='normal'):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                if mode == 'headers':
                    time.sleep(2)
                self.send_response(503 if mode == 'status' else 200)
                if mode not in ('unknown', 'slow', 'headers'):
                    self.send_header('Content-Length', str(len(body) + (10 if mode == 'truncated' else 0)))
                self.end_headers()
                if mode == 'slow':
                    for _ in range(100):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                        time.sleep(.05)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    instance = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{instance.server_port}/x.js'
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def execute(kind, url, body, tmp_path, monkeypatch, *, cap=10_000_000, seconds=3):
    monkeypatch.setattr(ja, 'TIMEOUT_SECONDS', seconds)
    monkeypatch.setattr(ja, 'MAX_BYTES', cap)
    if kind == 'native':
        try:
            return True, ja.download(url)
        except ValueError:
            return False, None
    entry = dict(url=url, sha256=hashlib.sha256(body).hexdigest(), sri=ja.sri(body), destination='out.js')
    monkeypatch.setattr(ja, 'cdn_url', lambda e: url)
    command = ja.oneliners(entry)[{'curl': 0, 'browser': 1, 'urllib': 2}[kind]]
    if kind == 'browser':
        if not shutil.which('node'):
            pytest.skip('Node is required to execute browser Web APIs')
        code = 'globalThis.crypto = require("node:crypto").webcrypto;\n(async () => {\ntry {\n'
        code += 'const result = ' + command + '; process.stdout.write(result);\n'
        code += '} catch (error) {console.error(String(error)); process.exitCode=1;}\n})();'
        returncode = subprocess.run(['node', '-e', code], cwd=tmp_path, capture_output=True, timeout=8)
        return returncode.returncode == 0, returncode.stdout if returncode.returncode == 0 else None
    if kind == 'curl' and not shutil.which('curl'):
        pytest.skip('curl required')
    # Run the emitted Python argument without requiring POSIX shell on Windows.
    import shlex
    args = shlex.split(command)
    args[0] = sys.executable
    done = subprocess.run(args, cwd=tmp_path, capture_output=True, timeout=8)
    target = tmp_path / 'out.js'
    return done.returncode == 0, target.read_bytes() if target.exists() else None


@pytest.mark.parametrize('kind', ['native', 'curl', 'browser', 'urllib'])
@pytest.mark.parametrize('delta', [0, 1])
def test_real_ten_mb_boundary(kind, delta, tmp_path, monkeypatch):
    # No Content-Length: the streaming cap must work without a trusted header.
    body = b'x' * (ja.MAX_BYTES + delta)
    with server(body, 'unknown') as url:
        ok, data = execute(kind, url, body, tmp_path, monkeypatch)
    assert ok is (delta == 0)
    assert data == (body if ok else None)


@pytest.mark.parametrize('kind', ['native', 'curl', 'browser', 'urllib'])
@pytest.mark.parametrize('mode', ['slow', 'headers', 'status', 'truncated', 'digest'])
def test_deadline_and_failures_preserve_destination(kind, mode, tmp_path, monkeypatch):
    if kind == 'native' and mode == 'digest':
        return  # download does not take an expected hash
    original = b'previous verified file'
    (tmp_path / 'out.js').write_bytes(original)
    body = b'abc'
    with server(body, mode) as url:
        start = time.monotonic()
        ok, data = execute(kind, url, b'wrong' if mode == 'digest' else body, tmp_path, monkeypatch,
                           cap=1024, seconds=.4)
        elapsed = time.monotonic() - start
    assert not ok
    assert elapsed < 1.8  # trickling data must not restart the total budget
    assert (tmp_path / 'out.js').read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['out.js']


@pytest.mark.parametrize(('expression', 'expected'), [
    ('(MIT OR GPL-3.0) AND GPL-3.0', False),
    ('MIT OR GPL-3.0 AND GPL-3.0', True),
    ('(MIT OR GPL-3.0) AND (ISC OR GPL-3.0)', True),
    ('MIT AND (ISC OR GPL-3.0)', True),
    ('(MIT AND GPL-3.0) OR (ISC AND GPL-3.0)', False),
    ('MIT OR (GPL-3.0 AND)', False), ('MIT OR', False),
    ('MIT OR ()', False), ('MIT OR (ISC', False), ('MIT) OR ISC', False),
    ('MIT WITH exception', False), ('MIT OR ISC WITH exception', False),
    ('MIT OR LicenseRef-local', False), ('MIT + ISC', False),
    ('MIT XOR ISC', False), ('MIT or ISC', False), ('MIT ISC', False),
    ('(' * 1000 + 'MIT' + ')' * 1000, False),
])
def test_license_grammar(expression, expected):
    assert ja.license_ok(expression) is expected


@pytest.mark.parametrize('expression', ['MIT OR (ISC', 'MIT OR ISC WITH exception', 'MIT OR LicenseRef-local'])
def test_review_cannot_override_unsupported_syntax(expression, tmp_path):
    seen = []
    with pytest.raises(ValueError, match='unsupported SPDX'):
        ja.add(ja.load_lock(tmp_path / 'lock'), tmp_path, 'a/b', 'v1', 'x.js', 'v',
               license=expression, allow_license=True, fetch=lambda u: seen.append(u))
    assert not seen and not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['curl', 'browser', 'urllib'])
def test_template_markers_and_quotes_in_url_are_literal(kind, tmp_path, monkeypatch):
    body = b'unchanged'
    with server(body) as url:
        url += "/CAP/SECONDS/URL/DEST/DIGEST/SRI/MILLISECONDS.js?q='x&v=$(literal)"
        ok, data = execute(kind, url, body, tmp_path, monkeypatch, cap=1024)
    assert ok and data == body


@pytest.mark.parametrize('kind', ['native', 'curl', 'browser', 'urllib'])
def test_known_oversize_is_refused(kind, tmp_path, monkeypatch):
    body = b'x' * 1025
    with server(body) as url:
        ok, _ = execute(kind, url, body, tmp_path, monkeypatch, cap=1024)
    assert not ok and not list(tmp_path.iterdir())
