#!/usr/bin/env python3
"""Bounded, opt-in DRAgoWing HTML dependency experiment; no app source changes.

prepare/verify never install packages. install requires an exclusive window and
uses only hash-locked binary PyPI wheels. workloads is offline HTML only.
This is not a generic installer, security sandbox, or full-repository test run.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import runpy
import shutil
import stat
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
UV = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/uv')
NAMES = ('index.html', 'propositions.html')
LIMITS = [
    'Planned up to three fresh-cache pairs, alternating AB/BA/AB; actual attempted/completed counts are recorded separately. OS/DNS/CDN caches not flushed.',
    'venv apparent/allocated bytes exclude symlink targets and the shared Python runtime.',
    'uv link-mode=copy avoids intentional cache-to-venv hardlinks; filesystem deduplication unknown.',
    'Cache bytes are retained filesystem size, NOT compressed network transfer bytes.',
    'Default includes dev tools; HTML feature does not. This is not an algorithm-only comparison.',
    'All HTML is generated offline; Plotly CDN references are emitted but never fetched/rendered.',
    'Native/stdlib parity is limited to supported fixtures; unsupported-domain errors need not match.',
    'Worker ru_maxrss is process high-water RSS, includes harness/imports, is not PSS.',
    'Sum-RSS is sampled from procfs; shared pages are double-counted and true peaks may be missed.',
    'Concurrency shares one host; not CPU/memory quota enforcement or general speed evidence.',
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('UV_', 'PIP_', 'PYTHON')) and k != 'VIRTUAL_ENV'}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONHASHSEED='0',
               POLARS_MAX_THREADS='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
               UV_HTTP_RETRIES='0', UV_PYTHON_DOWNLOADS='never')
    return env


def tree_bytes(root):
    result = dict(apparent_regular_bytes=0, allocated_regular_bytes=0,
                  regular_files=0, symlinks=0, symlink_text_bytes=0, hardlinked_files=0)
    if not root.exists():
        return result
    for p in root.rglob('*'):
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode):
            result['symlinks'] += 1
            result['symlink_text_bytes'] += s.st_size
        elif stat.S_ISREG(s.st_mode):
            result['regular_files'] += 1
            result['apparent_regular_bytes'] += s.st_size
            result['allocated_regular_bytes'] += s.st_blocks * 512
            result['hardlinked_files'] += s.st_nlink > 1
    return result


def verify():
    receipt = json.loads((BASE / 'source-receipt.json').read_text())
    for row in receipt['files']:
        p = BASE / row['destination']
        b = p.read_bytes()
        if sha(b) != row['sha256'] or len(b) != row['bytes']:
            raise ValueError('source/fixture/export identity mismatch: ' + str(p))
        if 'git_blob' in row:
            blob = hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest()
            if blob != row['git_blob']:
                raise ValueError('Git blob mismatch: ' + str(p))
    synthetic = BASE / 'synthetic-fixtures.json'
    if synthetic.is_file():
        for fixture, files in json.loads(synthetic.read_text())['fixtures'].items():
            for name, identity in files.items():
                data = (BASE / 'fixtures' / fixture / name).read_bytes()
                if sha(data) != identity['sha256'] or len(data) != identity['bytes']:
                    raise ValueError('synthetic fixture identity mismatch: ' + fixture + '/' + name)
    return {'verified_files': len(receipt['files']), 'receipt_sha256': sha((BASE / 'source-receipt.json').read_bytes()),
            'harness_sha256': sha(Path(__file__).read_bytes())}


def prepare():
    verify()
    # Synthetic edge fixture; not copied research data and not a new app oracle.
    rows = []
    for i in range(257):
        rows.append(dict(team='t' + str(i % 3), team_name='球団' + str(i % 3),
                         season=2000 + i // 3, league='C', upper_half=bool(i % 2),
                         rank_rf=1 + i % 6, rank_ra=1 + (i + 2) % 6,
                         wins_vs_pythag=[None, -0.0, 1.5, -2.0][i % 4],
                         rd=[None, -1, 0, 1][i % 4]))
    proposition = dict(id='P1', statement='Synthetic <edge> & 日本語',
                       **{'if': [dict(col='rd', op='>', value=0)],
                          'then': [dict(col='upper_half', op='==', value=True)]},
                       forms=[dict(form='original', n=10, hold=6, rate=0.6, counterexamples=[])])
    fixtures = {}
    for name, data in [('edge257', rows), ('one', rows[:1]),
                       ('unsupported', [dict(rows[0], rd=[1])])]:
        root = BASE / 'fixtures' / name
        root.mkdir(parents=True, exist_ok=False)
        (root / 'season.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False, allow_nan=False) + '\n' for x in data))
        (root / 'propositions.jsonl').write_text(json.dumps(proposition, ensure_ascii=False) + '\n')
        fixtures[name] = {p.name: {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size} for p in root.iterdir()}
    save(BASE / 'synthetic-fixtures.json', {'generator': Path(__file__).name, 'fixtures': fixtures})
    root = BASE / 'source/cycles/c001-chunichi'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'outputs').symlink_to(BASE / 'fixtures/c001', target_is_directory=True)


def install():
    verify()
    if subprocess.check_output([UV, '--version'], text=True).split()[1] != '0.13.4':
        raise ValueError('expected existing uv 0.13.4')
    root = BASE / 'install-trials'
    root.mkdir(exist_ok=False)
    records = []
    for trial, order in enumerate([('default', 'html'), ('html', 'default'), ('default', 'html')], 1):
        for profile in order:
            d = root / f'{trial}-{profile}'
            d.mkdir()
            venv, cache = d / 'venv', d / 'cache'
            start = time.perf_counter()
            created = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                                     env=clean_env(), capture_output=True, text=True, timeout=60)
            create_s = time.perf_counter() - start
            (d / 'venv.stdout.log').write_text(created.stdout)
            (d / 'venv.stderr.log').write_text(created.stderr)
            if created.returncode:
                raise RuntimeError('venv creation failed')
            command = [str(UV), '--no-config', 'pip', 'install', '--python', str(venv / 'bin/python'),
                       '--default-index', 'https://pypi.org/simple', '--require-hashes',
                       '--only-binary', ':all:', '--no-deps', '--link-mode', 'copy',
                       '--cache-dir', str(cache), '-r', str(BASE / f'requirements-{profile}.txt')]
            start = time.perf_counter()
            try:
                done = subprocess.run(command, env=clean_env(), capture_output=True, text=True, timeout=300)
                elapsed, code = time.perf_counter() - start, done.returncode
                (d / 'install.stdout.log').write_text(done.stdout)
                (d / 'install.stderr.log').write_text(done.stderr)
            except subprocess.TimeoutExpired as e:
                elapsed, code = time.perf_counter() - start, 'timeout'
                (d / 'install.stdout.log').write_bytes(e.stdout or b'')
                (d / 'install.stderr.log').write_bytes(e.stderr or b'')
            record = dict(trial=trial, profile=profile, order=list(order), command=command,
                          venv_create_seconds=create_s, install_seconds=elapsed, exit_code=code,
                          venv=tree_bytes(venv), cache=tree_bytes(cache), installed_packages=None)
            if code == 0:
                metadata = subprocess.check_output([str(venv / 'bin/python'), '-c',
                    'import importlib.metadata,json; print(json.dumps(sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())))'],
                    env=clean_env(), text=True)
                record['installed_packages'] = json.loads(metadata)
            records.append(record)
            save(d / 'receipt.json', record)
            save(BASE / 'install-results.json', dict(records=records, limits=LIMITS,
                python=sys.version, executable=sys.executable, platform=platform.platform(), uv='0.13.4',
                source=verify(), source_tree=tree_bytes(BASE / 'source'), network_transfer_bytes=None))
            if code != 0:
                raise RuntimeError('bounded install failed; no fallback: ' + str(d))


class JsonScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.key = None
        self.parts = []
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') == 'application/json':
            self.key = attrs.get('id')
            if not self.key or self.key in self.values:
                raise ValueError('missing/duplicate JSON script id')
            self.parts = []

    def handle_data(self, data):
        if self.key is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.key is not None:
            self.values[self.key] = json.loads(''.join(self.parts))
            self.key = None


def output_receipt(root):
    found = {p.name for p in root.iterdir() if p.is_file()}
    if found != set(NAMES):
        raise ValueError('unexpected or missing generated files: ' + str(found))
    result = {}
    for name in NAMES:
        b = (root / name).read_bytes()
        parser = JsonScripts()
        parser.feed(b.decode('utf-8'))
        parser.close()
        if parser.key is not None:
            raise ValueError('unterminated JSON script')
        canonical = json.dumps(parser.values, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
        result[name] = dict(bytes=len(b), sha256=sha(b), json_ids=sorted(parser.values),
                            structured_json_sha256=sha(canonical))
    if not result['index.html']['json_ids']:
        raise ValueError('index lost all embedded figure JSON')
    return result


def worker(args):
    # Explicit guard against accidental forbidden paths; not a full OS sandbox.
    events = []
    def audit(event, values):
        if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
            events.append(event)
            raise RuntimeError('offline HTML worker forbids ' + event)
    sys.addaudithook(audit)
    sys.path.insert(0, str(BASE / 'source'))
    cli = ['dragowing', '--results', str(BASE / 'fixtures' / args.fixture), '--out', str(args.output),
           '--overlap-backend', args.backend]
    sys.argv = cli
    start = time.perf_counter()
    code = 0
    error = None
    try:
        runpy.run_module('dragowing', run_name='__main__', alter_sys=True)
    except SystemExit as e:
        code = e.code or 0
    except Exception as e:
        code, error = 1, type(e).__name__ + ': ' + str(e)
        print(error, file=sys.stderr)
    elapsed = time.perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_SELF)
    loaded = sorted(set(sys.modules).intersection({'numpy', 'polars', 'propositions', 'rule_search', 'ask', 'markdown'}))
    receipt = dict(cli=cli, exit_code=code, error=error, elapsed_seconds=elapsed,
                   user_cpu_seconds=usage.ru_utime, system_cpu_seconds=usage.ru_stime,
                   process_max_rss_bytes=usage.ru_maxrss * (1024 if sys.platform.startswith('linux') else 1),
                   imports=loaded, forbidden_events=events, outputs=output_receipt(args.output) if code == 0 else None)
    if args.backend == 'stdlib' and {'numpy', 'polars', 'propositions', 'rule_search'}.intersection(loaded):
        raise RuntimeError('stdlib unexpectedly loaded native dependencies')
    save(args.receipt, receipt)
    return code


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    return 0


def batch(profile, backend, fixture, workers, label, tasks=2, environment_trial=3):
    d = BASE / 'workload-runs' / label
    d.mkdir(parents=True, exist_ok=False)
    interpreter = BASE / f'install-trials/{environment_trial}-{profile}/venv/bin/python'
    active, finished, samples = [], [], []
    started = time.perf_counter()
    submitted = 0
    try:
        while submitted < tasks or active:
            while submitted < tasks and len(active) < workers:
                run = d / str(submitted)
                run.mkdir()
                command = [str(interpreter), str(Path(__file__).resolve()), 'worker', '--fixture', fixture,
                           '--backend', backend, '--output', str(run / 'html'), '--receipt', str(run / 'receipt.json')]
                out, err = (run / 'stdout.log').open('wb'), (run / 'stderr.log').open('wb')
                try:
                    p = subprocess.Popen(command, env=clean_env(), cwd=BASE / 'source', stdout=out, stderr=err)
                except BaseException:
                    out.close(); err.close()
                    raise
                active.append((p, run, out, err, time.perf_counter()))
                submitted += 1
            samples.append(dict(seconds=time.perf_counter() - started, sum_rss_bytes=sum(rss(p.pid) for p, *_ in active)))
            for item in active[:]:
                p, run, out, err, start = item
                if p.poll() is None and time.perf_counter() - start > 120:
                    p.kill()
                    p.wait()
                if p.poll() is not None:
                    out.close(); err.close()
                    finished.append(dict(directory=str(run.relative_to(BASE)), exit_code=p.returncode,
                                         wall_seconds=time.perf_counter() - start))
                    active.remove(item)
            if active:
                time.sleep(0.01)
    finally:
        # A launch/sampling error must not leave workers running into another window.
        for p, run, out, err, start in active:
            try:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=10)
            finally:
                out.close(); err.close()
    result = dict(profile=profile, backend=backend, fixture=fixture, workers=workers, tasks=tasks,
                  source_install_trial=environment_trial,
                  wall_seconds=time.perf_counter() - started, runs=finished,
                  sampled_sum_rss_peak_bytes=max(x['sum_rss_bytes'] for x in samples), sample_interval_seconds=0.01)
    save(d / 'rss-samples.json', samples)
    save(d / 'batch.json', result)
    return result


def workloads(environment_trial=3):
    verify()
    for profile in ('default', 'html'):
        r = json.loads((BASE / f'install-trials/{environment_trial}-{profile}/receipt.json').read_text())
        if r['exit_code'] != 0 or r['trial'] != environment_trial or r['profile'] != profile:
            raise ValueError('selected environment is not a successful matching install')
    results, parity = [], []
    for fixture in ('c001', 'edge257', 'one'):
        compared = []
        for profile, backend in [('default', 'native'), ('default', 'stdlib'), ('html', 'stdlib')]:
            b = batch(profile, backend, fixture, 1, f'parity-{fixture}-{profile}-{backend}', tasks=1, environment_trial=environment_trial)
            results.append(b)
            r = json.loads((BASE / b['runs'][0]['directory'] / 'receipt.json').read_text())
            if r['exit_code'] or b['runs'][0]['exit_code']:
                raise RuntimeError('supported fixture failed: ' + str(b))
            compared.append(r['outputs'])
        if not compared[0] == compared[1] == compared[2]:
            raise RuntimeError('complete HTML/structured JSON mismatch: ' + fixture)
        parity.append(dict(fixture=fixture, exact_html_and_structured_json_equal=True, outputs=compared[0]))
        save(BASE / 'workload-results.json', dict(parity=parity, batches=results, limits=LIMITS))
    # Unsupported input is deliberately not a universal native-equivalence claim.
    unsupported = batch('html', 'stdlib', 'unsupported', 1, 'unsupported-html', tasks=1, environment_trial=environment_trial)
    r = json.loads((BASE / unsupported['runs'][0]['directory'] / 'receipt.json').read_text())
    if r['exit_code'] != 1 or r['outputs'] is not None:
        raise RuntimeError('unsupported input did not fail explicitly')
    results.append(unsupported)
    for trial in range(3):
        pairs = [('default', 'native'), ('default', 'stdlib'), ('html', 'stdlib')]
        if trial % 2:
            pairs.reverse()
        for workers in ((1, 2) if trial % 2 == 0 else (2, 1)):
            for profile, backend in pairs:
                b = batch(profile, backend, 'c001', workers, f'timing-{trial+1}-{profile}-{backend}-w{workers}', environment_trial=environment_trial)
                for run in b['runs']:
                    r = json.loads((BASE / run['directory'] / 'receipt.json').read_text())
                    if run['exit_code'] or r['outputs'] != parity[0]['outputs']:
                        raise RuntimeError('timing run failed parity')
                results.append(b)
                save(BASE / 'workload-results.json', dict(parity=parity, batches=results, limits=LIMITS))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify', 'install', 'workloads', 'worker'])
    parser.add_argument('--exclusive-window', action='store_true')
    parser.add_argument('--environment-trial', type=int, choices=[1, 2, 3], default=3,
                        help='explicit successful matched install pair for offline work; never retries installs')
    parser.add_argument('--fixture', choices=['c001', 'edge257', 'one', 'unsupported'])
    parser.add_argument('--backend', choices=['native', 'stdlib'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.mode in ('install', 'workloads') and not args.exclusive_window:
        parser.error('exclusive timing window required; coordinate before execution')
    if args.mode == 'worker':
        if not all((args.fixture, args.backend, args.output, args.receipt)):
            parser.error('worker requires explicit fixture/backend/output/receipt')
        return worker(args)
    if args.mode == 'verify':
        print(json.dumps(verify()))
    elif args.mode == 'workloads':
        workloads(args.environment_trial)
    else:
        globals()[args.mode]()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
