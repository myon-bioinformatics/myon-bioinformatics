#!/usr/bin/env python3
"""Opt-in bounded MCP comparison. Authorization and exclusive window are external.

Separate install/workloads commands; neither is a fallback for failure of the
other. One failed registry operation stops all remaining installs. No source
edits, dependency resolution/upgrades, alternate registry, model or browser.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time

import prepare_mcp_comparison as core

ROOT = Path(__file__).resolve().parent
UV = Path('<uv-0.13.4>')
RUNNER = ROOT / 'prepare_mcp_comparison.py'
LIMITS = [
    'Planned up to three fresh-cache AB/BA/AB pairs. Actual attempts/completions retained individually.',
    'OS/DNS/CDN caches not flushed; uv caches are fresh, but this is not a network throughput experiment.',
    'Apparent/allocated bytes exclude shared Python interpreter/stdlib and symlink targets; filesystem dedup unknown.',
    'Cache retained bytes are not compressed network transfer bytes; uv link-mode=copy.',
    'Default is full default server runtime, not optional test/browser/Galleria/model environments.',
    'Pruned caller still requires real MCP/Pydantic and their fixed transitive runtime dependencies.',
    'A separately measured full FastMCP server fixture remains required; this is not whole-service removal.',
    'Two environments co-hosted cost full-server bytes plus client bytes, potentially more than one shared full environment.',
    'Each batch has exactly two fixed client jobs against one real server; startup excluded from workload wall time.',
    'Client wall time includes process startup/imports/calls/receipt writing; algorithm and server are unchanged.',
    'RSS sampled every 20ms; sum RSS double-counts shared pages and can miss true peaks. Not PSS or enforced quotas.',
    'Client ru_maxrss is sampled after workload, before receipt packaging; not final process-lifetime peak.',
    'No external API/model/browser/Docker; loopback audit hooks are defense in depth, not an OS sandbox.',
    'Output equivalence is confined to eight synthetic cases through both chat/direct paths.',
]


def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('UV_', 'PIP_', 'PYTHON')) and k != 'VIRTUAL_ENV'}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONHASHSEED='0',
               UV_HTTP_RETRIES='0', UV_HTTP_TIMEOUT='60', UV_PYTHON_DOWNLOADS='never')
    return env


def workload_env():
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR') if k in os.environ}
    env.update(HOME=str(ROOT / 'workload-home'), PYTHONDONTWRITEBYTECODE='1',
               PYTHONNOUSERSITE='1', PYTHONHASHSEED='0', NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')
    return env


def tree_bytes(root):
    result = dict(apparent_regular_bytes=0, allocated_regular_bytes=0, regular_files=0,
                  symlinks=0, symlink_text_bytes=0, hardlinked_files=0)
    if not root.exists():
        return result
    for path in root.rglob('*'):
        s = path.lstat()
        if stat.S_ISLNK(s.st_mode):
            result['symlinks'] += 1
            result['symlink_text_bytes'] += s.st_size
        elif stat.S_ISREG(s.st_mode):
            result['regular_files'] += 1
            result['apparent_regular_bytes'] += s.st_size
            result['allocated_regular_bytes'] += s.st_blocks * 512
            result['hardlinked_files'] += s.st_nlink > 1
    return result


def identities():
    return {**core.verify(), 'worker_sha256': core.digest(RUNNER.read_bytes()),
            'orchestrator_sha256': core.digest(Path(__file__).read_bytes())}


def verify_unchanged(initial):
    if identities() != initial:
        raise ValueError('source/export/harness identity changed; stop without fallback')


def install():
    source = identities()
    if subprocess.check_output([UV, '--version'], text=True).split()[1] != '0.13.4':
        raise ValueError('expected existing uv 0.13.4')
    root = ROOT / 'install-trials'
    root.mkdir(exist_ok=False)  # No overwrites or silent resume/retry.
    records = []
    postrun_identity_verified = False
    for trial, order in enumerate([('server', 'client'), ('client', 'server'), ('server', 'client')], 1):
        for profile in order:
            verify_unchanged(source)
            folder = root / f'{trial}-{profile}'
            folder.mkdir()
            venv, cache = folder / 'venv', folder / 'cache'
            record = {'trial': trial, 'profile': profile, 'order': list(order), 'stage': 'venv_creation',
                      'venv_create_seconds': None, 'venv_create_exit_code': None,
                      'install_seconds': None, 'exit_code': None, 'installed_packages': None}
            records.append(record)

            def save():
                record['venv'] = tree_bytes(venv)
                record['cache'] = tree_bytes(cache)
                core.write_json(folder / 'receipt.json', record)
                core.write_json(ROOT / 'install-results.json', {'records': records, 'source': source,
                    'python': sys.version, 'python_executable': sys.executable, 'platform': platform.platform(),
                    'uv': '0.13.4', 'network_transfer_bytes': None, 'limits': LIMITS,
                    'postrun_identity_verified': postrun_identity_verified})

            save()
            created_at = time.perf_counter()
            with (folder / 'venv.stdout.log').open('wb') as out, (folder / 'venv.stderr.log').open('wb') as err:
                try:
                    result = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                                            env=clean_env(), stdout=out, stderr=err, timeout=60)
                    record['venv_create_exit_code'] = result.returncode
                except subprocess.TimeoutExpired:
                    record['venv_create_exit_code'] = 'timeout'
            record['venv_create_seconds'] = time.perf_counter() - created_at
            save()
            if record['venv_create_exit_code'] != 0:
                raise RuntimeError('venv creation failed; no fallback')
            command = [str(UV), '--no-config', 'pip', 'install', '--python', str(venv / 'bin/python'),
                       '--default-index', 'https://pypi.org/simple', '--require-hashes', '--only-binary', ':all:',
                       '--no-deps', '--link-mode', 'copy', '--cache-dir', str(cache),
                       '-r', str(ROOT / f'requirements-{profile}.txt')]
            record.update(command=command, stage='installing')
            save()
            verify_unchanged(source)  # Immediately before each network operation, outside timing.
            started = time.perf_counter()
            with (folder / 'install.stdout.log').open('wb') as out, (folder / 'install.stderr.log').open('wb') as err:
                try:
                    result = subprocess.run(command, env=clean_env(), stdout=out, stderr=err, timeout=300)
                    record['exit_code'] = result.returncode
                except subprocess.TimeoutExpired:
                    record['exit_code'] = 'timeout'
            record['install_seconds'] = time.perf_counter() - started
            record['stage'] = 'installed' if record['exit_code'] == 0 else 'failed'
            save()
            if record['exit_code'] != 0:
                raise RuntimeError('bounded registry install failed; all later attempts remain unstarted')
            verify_unchanged(source)
            metadata = subprocess.check_output([str(venv / 'bin/python'), '-I', '-B', '-c',
                'import importlib.metadata,json; print(json.dumps(sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())))'],
                env=clean_env(), text=True, timeout=20)
            record['installed_packages'] = json.loads(metadata)
            save()
    verify_unchanged(source)
    postrun_identity_verified = True
    save()


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    return 0


def reap(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.returncode


def validate_calls(path, tags):
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    calls = [row for row in events if row.get('event') == 'tools/call']
    expected = {}
    for tag in tags:
        for name, tool, arguments, is_error in core.CASES:
            for mode in ('direct', 'chat'):
                expected[f'nano-{tag}-{name}-{mode}'] = (tool, arguments, mode, is_error)
    if len(calls) != len(expected):
        raise AssertionError('wrong number of real server calls')
    seen = set()
    for row in calls:
        trace = row.get('meta', {}).get('trace_id')
        if trace not in expected or trace in seen:
            raise AssertionError('missing/duplicate/unexpected server trace')
        seen.add(trace)
        tool, arguments, mode, is_error = expected[trace]
        if row['tool'] != tool or row['arguments'] != arguments or row['meta']['source'] != mode:
            raise AssertionError('server trace input mismatch')
        if (row['outcome'] == 'error') != is_error:
            raise AssertionError('server trace outcome mismatch')
    return {'calls': len(calls), 'events': len(events), 'trace_ids': sorted(seen),
            'log_sha256': core.digest(path.read_bytes()), 'external_source_calls': 0}


def batch(folder, server_python, client_python, workers, reference=None):
    folder.mkdir()
    processes = []
    server = None
    receipts = []
    tags = [folder.name + '-job-' + str(n) for n in range(2)]
    result = {'status': 'started', 'workers': workers, 'jobs': 2, 'worker_exit_codes': [],
              'server_exit_code': None, 'sampled_client_sum_rss_max_bytes': 0,
              'sampled_server_rss_max_bytes': 0, 'sampled_combined_sum_rss_max_bytes': 0}
    started = None
    with ExitStack() as files:
        try:
            ready, log = folder / 'server-ready.json', folder / 'server.jsonl'
            command = [str(server_python), '-I', '-B', str(RUNNER), 'serve', '--ready', str(ready),
                       '--log', str(log), '--allow-loopback-execution']
            server = subprocess.Popen(command, env=workload_env(), stdout=files.enter_context((folder / 'server.stdout.log').open('wb')),
                                      stderr=files.enter_context((folder / 'server.stderr.log').open('wb')))
            result['server_command'] = command
            wait_started = time.perf_counter()
            while not ready.exists():
                if server.poll() is not None or time.perf_counter() - wait_started > 25:
                    raise RuntimeError('fixture server startup failed/timed out')
                time.sleep(0.02)
            url = json.loads(ready.read_text())['url']
            core.endpoint(url)
            result['server_startup_seconds'] = time.perf_counter() - wait_started
            result['server_ready_rss_bytes'] = rss(server.pid)
            active = []
            next_job = 0
            started = time.perf_counter()
            while next_job < 2 or active:
                while next_job < 2 and len(active) < workers:
                    n = next_job
                    output = folder / f'client-{n}.json'
                    command = [str(client_python), '-I', '-B', str(RUNNER), 'client', '--url', url,
                               '--output', str(output), '--run-tag', tags[n], '--allow-loopback-execution']
                    proc = subprocess.Popen(command, env=workload_env(), stdout=files.enter_context((folder / f'client-{n}.stdout.log').open('wb')),
                                            stderr=files.enter_context((folder / f'client-{n}.stderr.log').open('wb')))
                    processes.append(proc)
                    active.append((n, proc, output))
                    next_job += 1
                client_rss = sum(rss(proc.pid) for _, proc, _ in active)
                server_rss = rss(server.pid)
                result['sampled_client_sum_rss_max_bytes'] = max(result['sampled_client_sum_rss_max_bytes'], client_rss)
                result['sampled_server_rss_max_bytes'] = max(result['sampled_server_rss_max_bytes'], server_rss)
                result['sampled_combined_sum_rss_max_bytes'] = max(result['sampled_combined_sum_rss_max_bytes'], client_rss + server_rss)
                for n, proc, output in list(active):
                    if proc.poll() is not None:
                        result['worker_exit_codes'].append({'job': n, 'exit_code': proc.returncode})
                        if proc.returncode != 0:
                            raise RuntimeError(f'client {n} failed')
                        receipt = json.loads(output.read_text())
                        if reference is not None and receipt['output'] != reference:
                            raise AssertionError('complete real MCP/chat output parity failed')
                        if reference is None:
                            reference = receipt['output']
                        receipts.append(receipt)
                        active.remove((n, proc, output))
                if server.poll() is not None:
                    raise RuntimeError('fixture server terminated early')
                if time.perf_counter() - started > 180:
                    raise TimeoutError('bounded client batch timed out')
                time.sleep(0.02)
            result['wall_seconds'] = time.perf_counter() - started
            result['server_trace'] = validate_calls(log, tags)
            result['output_sha256'] = core.digest(core.canonical(reference))
            result['client_receipts'] = receipts
            result['status'] = 'completed'
            return result, reference
        except BaseException as exc:
            result['status'] = 'failed'
            result['error'] = repr(exc)
            if started is not None:
                result['wall_seconds_until_failure'] = time.perf_counter() - started
            raise
        finally:
            # Covers launch failures, parse errors, test failures, cancellation.
            for process in processes:
                reap(process)
            if server is not None:
                result['server_exit_code'] = reap(server)
            result['all_clients_final_exit_codes'] = [p.returncode for p in processes]
            core.write_json(folder / 'batch-receipt.json', result)


def workloads(environment_trial):
    source = identities()
    install_rows = json.loads((ROOT / 'install-results.json').read_text())['records']
    pair = {r['profile']: r for r in install_rows if r['trial'] == environment_trial and r['exit_code'] == 0}
    if set(pair) != {'server', 'client'}:
        raise ValueError('selected environment is not a complete successful matched pair')
    server_packages = dict(pair['server']['installed_packages'])
    client_packages = dict(pair['client']['installed_packages'])
    for name, version in client_packages.items():
        if server_packages.get(name) != version:
            raise ValueError('client/common versions diverged: ' + name)
    root = ROOT / 'workload-runs'
    root.mkdir(exist_ok=False)
    (ROOT / 'workload-home').mkdir(exist_ok=True)
    rows = []
    reference = None
    server_python = ROOT / 'install-trials' / f'{environment_trial}-server/venv/bin/python'
    summary = {'selected_environment_trial': environment_trial, 'source': source, 'records': rows,
               'postrun_identity_verified': False,
               'limits': LIMITS, 'model_inference_performed': False, 'browser_started': False,
               'docker_started': False, 'external_api_calls': 0,
               'installed_environment_accounting': {
                   'shared_full_environment_apparent_bytes': pair['server']['venv']['apparent_regular_bytes'],
                   'client_only_apparent_bytes': pair['client']['venv']['apparent_regular_bytes'],
                   'separate_fixture_apparent_bytes': pair['server']['venv']['apparent_regular_bytes'],
                   'cohost_separate_environments_apparent_bytes': pair['server']['venv']['apparent_regular_bytes'] + pair['client']['venv']['apparent_regular_bytes']}}
    for repetition in range(1, 4):
        for workers in (1, 2):
            order = ('server', 'client') if repetition % 2 else ('client', 'server')
            for profile in order:
                verify_unchanged(source)
                folder = root / f'r{repetition}-w{workers}-{profile}'
                python = ROOT / 'install-trials' / f'{environment_trial}-{profile}/venv/bin/python'
                try:
                    row, reference = batch(folder, server_python, python, workers, reference)
                    verify_unchanged(source)
                    row.update(repetition=repetition, profile=profile, order=list(order))
                    rows.append(row)
                finally:
                    core.write_json(ROOT / 'workload-results.json', summary)
    core.write_json(ROOT / 'output-reference.json', reference)
    verify_unchanged(source)
    summary['postrun_identity_verified'] = True
    core.write_json(ROOT / 'workload-results.json', summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['install', 'workloads'])
    parser.add_argument('--exclusive-window', action='store_true')
    parser.add_argument('--environment-trial', type=int)
    args = parser.parse_args(argv)
    if not args.exclusive_window:
        parser.error('exclusive measurement window must be released by coordinator first')
    if args.mode == 'install':
        if args.environment_trial is not None:
            parser.error('environment trial is a workload selector only')
        install()
    else:
        if args.environment_trial not in (1, 2, 3):
            parser.error('explicit completed environment trial is required')
        workloads(args.environment_trial)


if __name__ == '__main__':
    main()
