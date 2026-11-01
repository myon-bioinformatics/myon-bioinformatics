#!/usr/bin/env python3
"""Bounded source-intact Gradio API worker; never install or launch a browser.

verify is stdlib-only. api/original-tests need separate permission and an
exclusive measurement window. Flags are operator gates, not authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import resource
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
REVISION = 'f04d233d2edc63650439fbb7692434e9f2c66266'
CURL = '/usr/bin/curl'
CASES = {
    'rich': '# Title\n\n[docs](https://example.com)\n\n![alt text](img.png)\n',
    'empty': '',
    'unicode_code': '# 日本語\n\n## Second\n\n```text\nliteral\n```\n',
}
EXPECTED = {
    'rich': ('# Title', '- docs: https://example.com', '- alt text: img.png',
             {'heading_count': 1, 'link_count': 1, 'image_count': 1, 'code_block_count': 0,
              'raw_html_count': 0, 'urls': ['https://example.com', 'img.png']}),
    'empty': ('(none)', '(none)', '(none)',
              {'heading_count': 0, 'link_count': 0, 'image_count': 0, 'code_block_count': 0,
               'raw_html_count': 0, 'urls': []}),
    'unicode_code': ('# 日本語\n## Second', '(none)', '(none)',
                     {'heading_count': 2, 'link_count': 0, 'image_count': 0, 'code_block_count': 1,
                      'raw_html_count': 0, 'urls': []}),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def verify():
    receipt = json.loads((ROOT / 'source-receipt.json').read_text())
    if receipt['revision'] != REVISION:
        raise ValueError('wrong source revision')
    for row in receipt['files']:
        path = ROOT / row['path']
        data = path.read_bytes()
        if path.is_symlink() or len(data) != row['bytes'] or sha(data) != row['sha256']:
            raise ValueError('source/export identity mismatch: ' + row['path'])
        if row.get('git_blob'):
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            if blob != row['git_blob']:
                raise ValueError('source Git blob mismatch')
    tools = json.loads((ROOT / 'test-tools-receipt.json').read_text())
    expected = {row['path'] for row in tools['files']}
    actual = {str(path.relative_to(ROOT / 'test-tools')) for path in (ROOT / 'test-tools').rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('unexpected/missing test-tool file')
    for row in tools['files']:
        path = ROOT / 'test-tools' / row['path']
        data = path.read_bytes()
        if path.is_symlink() or len(data) != row['bytes'] or sha(data) != row['sha256']:
            raise ValueError('test-tool identity mismatch: ' + row['path'])
    return {'revision': REVISION, 'source_receipt_sha256': sha((ROOT / 'source-receipt.json').read_bytes()),
            'test_tools_receipt_sha256': sha((ROOT / 'test-tools-receipt.json').read_bytes()),
            'worker_sha256': sha(Path(__file__).read_bytes())}


def isolated_env(folder):
    folder = Path(folder).resolve()
    env = {name: os.environ[name] for name in ('PATH', 'LANG', 'LC_ALL', 'TZ') if name in os.environ}
    for name, child in [('HOME', 'home'), ('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache'),
                        ('XDG_CONFIG_HOME', 'config'), ('HF_HOME', 'hf'), ('GRADIO_TEMP_DIR', 'gradio-tmp')]:
        path = folder / child
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONHASHSEED='0',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', GRADIO_ANALYTICS_ENABLED='False',
               HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_OFFLINE='1', DO_NOT_TRACK='1',
               GRADIO_SERVER_NAME='127.0.0.1', GRADIO_SSR_MODE='False', GRADIO_SHARE='False',
               GRADIO_NUM_PORTS='1', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    return env


def local_url(url, ports):
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == 'http' and p.hostname == '127.0.0.1' and p.port in ports
                and p.username is None and p.password is None and not p.query and not p.fragment
                and re.fullmatch(r'/gradio_api/call/analyze(?:/[A-Za-z0-9_-]{1,128})?', p.path) is not None)
    except (ValueError, TypeError):
        return False


def allowed_curl(executable, argv, ports):
    if str(executable) != CURL or not isinstance(argv, (tuple, list)) or not argv or str(argv[0]) != CURL:
        return False
    argv = list(argv)
    if len(argv) == 9 and argv[1:4] == ['-s', '-X', 'POST'] and argv[5:7] == ['-H', 'Content-Type: application/json'] and argv[7] == '-d':
        if not local_url(argv[4], ports) or not argv[4].endswith('/analyze'):
            return False
        try:
            payload = json.loads(argv[8])
            return payload in [{'data': [CASES['rich'], None]}, {'data': ['', None]}]
        except (TypeError, ValueError):
            return False
    return (len(argv) == 6 and argv[1:4] == ['-s', '-N', '--max-time'] and argv[4] == '15.0'
            and local_url(argv[5], ports) and '/analyze/' in argv[5])


class Audit:
    def __init__(self, path):
        self.path = Path(path)
        self.ports = set()
        self.denied = 0
        self.curl_calls = 0
        self.lock = threading.Lock()

    def decide(self, event, args):
        if event == 'socket.bind':
            address = args[1]
            ok = isinstance(address, tuple) and address[0] == '127.0.0.1'
            if ok and address[1]:
                self.ports.add(address[1])
            return ok, repr(address)
        if event == 'socket.connect':
            address = args[1]
            return (isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] in self.ports), repr(address)
        if event == 'socket.getaddrinfo':
            try:
                ok = args[0] in ('127.0.0.1', b'127.0.0.1', 'localhost', b'localhost') and int(args[1]) in self.ports
            except (ValueError, TypeError):
                ok = False
            return ok, repr(args[:2])
        if event in ('socket.gethostbyname', 'socket.gethostbyaddr'):
            return args[0] in ('127.0.0.1', b'127.0.0.1'), repr(args[:1])
        if event == 'subprocess.Popen':
            ok = allowed_curl(args[0], args[1], self.ports)
            if ok:
                self.curl_calls += 1
            return ok, repr(args[:2])[:1000]
        if event in ('os.system', 'os.posix_spawn', 'os.exec', 'os.fork'):
            return False, 'child execution not allowed'
        return None, None

    def __call__(self, event, args):
        ok, target = self.decide(event, args)
        if ok is None:
            return
        with self.lock:
            if not ok:
                self.denied += 1
            # Incremental persistence even if a thread catches the exception or process is killed later.
            with self.path.open('a', encoding='utf-8') as out:
                out.write(json.dumps({'event': event, 'target': target, 'allowed': ok}) + '\n')
                out.flush()
        if not ok:
            raise PermissionError('Gradio comparison blocked ' + event)


def parse_sse(text):
    result = []
    for block in text.replace('\r\n', '\n').split('\n\n'):
        event = None
        data = []
        for line in block.splitlines():
            if line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if event == 'error':
            raise ValueError('Gradio SSE error: ' + '\n'.join(data))
        if event == 'complete':
            result.append(json.loads('\n'.join(data)))
    if len(result) != 1 or not isinstance(result[0], list) or len(result[0]) != 4:
        raise ValueError('expected exactly one complete four-field Gradio result')
    return result[0]


def validate_result(name, result):
    expected = EXPECTED[name]
    if (not isinstance(result, (tuple, list)) or len(result) != 4 or
            list(result[:3]) != list(expected[:3]) or result[3] != json.dumps(expected[3], indent=2)):
        raise AssertionError('complete independent fixed fixture mismatch: ' + name)


def raw_http_call(base_url, text, folder, name):
    request = urllib.request.Request(base_url + '/gradio_api/call/analyze',
        data=json.dumps({'data': [text, None]}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        post = response.read(65537)
        if response.status != 200 or len(post) > 65536:
            raise ValueError('unexpected job-submission response')
    (folder / (name + '.post.json')).write_bytes(post)
    event_id = json.loads(post)['event_id']
    if not isinstance(event_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event_id) is None:
        raise ValueError('invalid opaque event id')
    with urllib.request.urlopen(base_url + '/gradio_api/call/analyze/' + event_id, timeout=15) as response:
        stream = response.read(2_000_001)
        if response.status != 200 or len(stream) > 2_000_000:
            raise ValueError('unexpected SSE response')
    (folder / (name + '.sse.txt')).write_bytes(stream)
    return parse_sse(stream.decode())


def activate_source():
    sys.path[:0] = [str(ROOT / 'source'), str(ROOT / 'source/demos')]
    import gradio_app
    if Path(gradio_app.__file__).resolve() != ROOT / 'source/demos/gradio_app.py':
        raise ValueError('wrong app implementation')
    if Path(gradio_app.md.__file__).resolve() != ROOT / 'source/markdown.py':
        raise ValueError('wrong Markdown implementation')
    return gradio_app


def runtime_identity():
    expected = {'gradio': '6.30.0', 'gradio-client': '2.7.2'}
    for name, version in expected.items():
        if importlib.metadata.version(name) != version:
            raise ValueError('wrong installed runtime version: ' + name)
    return {'versions': expected, 'python_prefix': sys.prefix}


def close_app(client, demo):
    operations = []
    if client is not None:
        operations.append(client.close)
    # SSE-v3 readers can stay open after the last prediction. Close the server
    # before waiting on their executor, otherwise orderly cleanup can deadlock.
    operations.append(demo.close)
    if client is not None:
        for executor in (client.executor, client.helper_executor, client.stream_executor):
            operations.append(lambda executor=executor: executor.shutdown(wait=True, cancel_futures=True))
    failures = []
    for operation in operations:
        try:
            operation()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


def api(folder, audit):
    started = time.perf_counter()
    app = activate_source()
    import gradio_client
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Original tests use the same reserve/release pattern. No retry on collision.
    audit.ports.add(port)
    demo = app.build_app()
    client = None
    results = []
    try:
        demo.queue()
        demo.launch(server_name='127.0.0.1', server_port=port, prevent_thread_lock=True,
                    show_error=True, quiet=True, inbrowser=False, share=False)
        base = f'http://127.0.0.1:{port}'
        client = gradio_client.Client(base, analytics_enabled=False, verbose=False)
        for name, text in CASES.items():
            via_http = raw_http_call(base, text, folder, name)
            via_client = list(client.predict(text, None, api_name='/analyze'))
            validate_result(name, via_http)
            validate_result(name, via_client)
            if via_client != via_http:
                raise AssertionError('real queue routes disagree')
            results.append({'case': name, 'input': text, 'http_complete_payload': via_http,
                            'gradio_client_complete_payload': via_client})
    finally:
        close_app(client, demo)
    if audit.denied:
        raise AssertionError('workload attempted forbidden activity')
    return {'output': results, 'output_sha256': sha(canonical(results)),
            'seconds_import_launch_api_shutdown': time.perf_counter() - started,
            'loopback_port': port, 'api_calls': 2 * len(CASES), 'denied_attempts': audit.denied,
            'curl_calls': audit.curl_calls, 'cli_completion_ru_maxrss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'app_path': str(Path(app.__file__).resolve()), 'markdown_path': str(Path(app.md.__file__).resolve())}


def original_tests(folder, audit):
    if shutil.which('curl') != CURL:
        raise ValueError('exact preflight curl executable unavailable; no skip masking')
    sys.path.insert(0, str(ROOT / 'test-tools'))
    activate_source()
    import pytest
    import gradio
    import gradio_client
    if not Path(pytest.__file__).resolve().is_relative_to(ROOT / 'test-tools'):
        raise ValueError('pytest not isolated in separately accounted test-tools')
    for module in (gradio, gradio_client):
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError('runtime package leaked from another environment')
    report = folder / 'junit.xml'
    result = pytest.main(['-q', '-p', 'no:cacheprovider', '--junitxml=' + str(report),
                          str(ROOT / 'source/tests/frontend/test_gradio_curl.py'),
                          str(ROOT / 'source/tests/frontend/test_gradio_ui.py')])
    tree = ET.parse(report).getroot()
    cases = list(tree.iter('testcase'))
    skipped = list(tree.iter('skipped'))
    failures = list(tree.iter('failure')) + list(tree.iter('error'))
    if result != 0 or len(cases) != 4 or skipped or failures or audit.denied or audit.curl_calls != 4:
        raise AssertionError('original four-test no-skip acceptance failed')
    return {'pytest_exit_code': int(result), 'passed': 4, 'skipped': 0, 'failures': 0,
            'curl_calls': audit.curl_calls, 'denied_attempts': audit.denied,
            'pytest_path': pytest.__file__, 'gradio_path': gradio.__file__, 'gradio_client_path': gradio_client.__file__,
            'junit_sha256': sha(report.read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['verify', 'api', 'original-tests'])
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--allow-loopback-execution', action='store_true')
    args = parser.parse_args(argv)
    if args.mode != 'verify' and not args.allow_loopback_execution:
        parser.error('separate permission and exclusive window required')
    identity = verify()
    if args.mode == 'verify':
        print(json.dumps(identity, indent=2))
        return 0
    if args.folder is None or not args.folder.is_dir():
        parser.error('existing private per-worker folder required')
    # Parent supplies isolation before Python starts; enforce same clean set before package imports.
    clean = isolated_env(args.folder)
    os.environ.clear()
    os.environ.update(clean)
    audit = Audit(args.folder / 'audit.jsonl')
    sys.addaudithook(audit)
    record = {'status': 'started', 'source': identity, 'python': sys.version,
              'model_inference_performed': False, 'browser_started': False, 'docker_started': False}
    try:
        record['runtime'] = runtime_identity()
        record.update(api(args.folder, audit) if args.mode == 'api' else original_tests(args.folder, audit))
        if verify() != identity:
            raise ValueError('source/harness/test-tools changed during work')
        record['status'] = 'completed'
    except BaseException as exc:
        record.update(status='failed', error=repr(exc))
        raise
    finally:
        record.update(denied_attempts=audit.denied, curl_calls=audit.curl_calls,
                      audit_sha256=sha((args.folder / 'audit.jsonl').read_bytes()) if (args.folder / 'audit.jsonl').exists() else None)
        save(args.folder / 'receipt.json', record)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
