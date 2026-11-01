#!/usr/bin/env python3
"""Source-intact markdown Gradio experiment: six bounded installs, local API only."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time
import gradio_worker as core
ROOT = Path(__file__).resolve().parent
UV = Path('<uv-0.13.4>')
RUNNER = ROOT / 'gradio_worker.py'
LIMITS = [
    'Planned three fresh-cache AB/BA/AB install pairs; retain actual attempts and stop first failure.',
    'Both profiles select only frontend with no default dev groups and no project; feature prunes Streamlit/Playwright.',
    'Apparent/allocated regular-file bytes exclude shared interpreter/stdlib and symlink targets.',
    'uv link-mode=copy; filesystem dedup unknown. Retained cache bytes are not network transfer bytes.',
    'OS/DNS/CDN caches not flushed; timing is a small host observation, not universal speed evidence.',
    'Both sides execute unchanged real Gradio queue/HTTP/client routes; no browser or model.',
    'Each fixed job owns a private real local server plus HOME/TMPDIR/cache/output; no external server omitted.',
    'Batch time includes two process starts, imports, app launch, API work, shutdown and receipt writing.',
    'Only whole four-field callback payloads compare exactly; opaque job/session IDs are retained separately, not compared as content.',
    'RSS sampled every20ms; sum double-counts shared pages, can miss peaks, is not PSS or a resource quota.',
    'Worker high-water is read after API shutdown before receipt serialization, not final process-lifetime peak.',
    'Test-only reused pytest/iniconfig/pluggy files and host curl are separately accounted, not installed in runtime venvs.',
    'Full child/browser suite is untouched and not run; four existing no-browser tests must pass in each profile without skips.',
    'No external API/model/browser/Docker or child repository changes.',
]


def clean_env(folder):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('UV_', 'PIP_', 'PYTHON')) and k != 'VIRTUAL_ENV'}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONHASHSEED='0',
               UV_HTTP_RETRIES='0', UV_HTTP_TIMEOUT='60', UV_PYTHON_DOWNLOADS='never')
    for name, child in [('HOME', 'installer-home'), ('TMPDIR', 'installer-tmp'),
                        ('XDG_CONFIG_HOME', 'installer-config'), ('XDG_CACHE_HOME', 'installer-xdg-cache')]:
        path = Path(folder) / child
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
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
    return {**core.verify(), 'worker_sha256': core.sha(RUNNER.read_bytes()),
            'orchestrator_sha256': core.sha(Path(__file__).read_bytes())}

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
    for trial, order in enumerate([('full', 'gradio'), ('gradio', 'full'), ('full', 'gradio')], 1):
        for profile in order:
            verify_unchanged(source)
            folder = root / f'{trial}-{profile}'
            folder.mkdir()
            venv, cache = folder / 'venv', folder / 'cache'
            env = clean_env(folder)  # Private installer state created before either timer.
            record = {'trial': trial, 'profile': profile, 'order': list(order), 'stage': 'venv_creation',
                      'venv_create_seconds': None, 'venv_create_exit_code': None,
                      'install_seconds': None, 'exit_code': None, 'installed_packages': None,
                      'installer_private_paths': {k:env[k] for k in ('HOME','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME')}}
            records.append(record)

            def save():
                record['venv'] = tree_bytes(venv)
                record['cache'] = tree_bytes(cache)
                core.save(folder / 'receipt.json', record)
                core.save(ROOT / 'install-results.json', {'records': records, 'source': source,
                    'python': sys.version, 'python_executable': sys.executable, 'platform': platform.platform(),
                    'uv': '0.13.4', 'network_transfer_bytes': None, 'limits': LIMITS,
                    'postrun_identity_verified': postrun_identity_verified})

            save()
            created_at = time.perf_counter()
            with (folder / 'venv.stdout.log').open('wb') as out, (folder / 'venv.stderr.log').open('wb') as err:
                try:
                    result = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                                            env=env, stdout=out, stderr=err, timeout=60)
                    record['venv_create_exit_code'] = result.returncode
                except subprocess.TimeoutExpired:
                    record['venv_create_exit_code'] = 'timeout'
                except OSError as exc:
                    record.update(venv_create_exit_code='launch_error', stage='venv_launch_failed',
                                  error=repr(exc), attempted_child_execution='known_not_started')
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
                    result = subprocess.run(command, env=env, stdout=out, stderr=err, timeout=300)
                    record['exit_code'] = result.returncode
                except subprocess.TimeoutExpired:
                    record['exit_code'] = 'timeout'
                except OSError as exc:
                    record.update(exit_code='launch_error', error=repr(exc),
                                  attempted_child_execution='known_not_started')
            record['install_seconds'] = time.perf_counter() - started
            record['stage'] = 'installed' if record['exit_code'] == 0 else 'failed'
            save()
            if record['exit_code'] != 0:
                raise RuntimeError('bounded registry install failed; all later attempts remain unstarted')
            verify_unchanged(source)
            metadata = subprocess.check_output([str(venv / 'bin/python'), '-I', '-B', '-c',
                'import importlib.metadata,json; print(json.dumps(sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())))'],
                env=env, text=True, timeout=20)
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
    # Always target the owned group, even if its Python leader has already exited.
    # A curl child from an unchanged test may still be alive in that group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    else:
        process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.02)
    return process.returncode


def batch(folder, python, workers, reference=None, mode='api'):
    folder.mkdir()
    processes = []
    active = []
    count = 2 if mode == 'api' else 1
    result = {'status': 'started', 'workers': workers, 'jobs': count, 'mode': mode,
              'sampled_sum_rss_max_bytes': 0, 'worker_exit_codes': [], 'worker_receipts': []}
    started = time.perf_counter()
    with ExitStack() as files:
        try:
            next_job = 0
            while next_job < count or active:
                while next_job < count and len(active) < workers:
                    n = next_job
                    work = folder / f'job-{n}'
                    work.mkdir()
                    env = core.isolated_env(work)
                    command = [str(python), '-I', '-B', str(RUNNER), mode, '--folder', str(work), '--allow-loopback-execution']
                    out = files.enter_context((work / 'stdout.log').open('wb'))
                    err = files.enter_context((work / 'stderr.log').open('wb'))
                    proc = subprocess.Popen(command, env=env, cwd=work, stdout=out, stderr=err, start_new_session=True)
                    processes.append(proc)
                    active.append((n, proc, work))
                    next_job += 1
                current_rss = sum(rss(proc.pid) for _, proc, _ in active)
                result['sampled_sum_rss_max_bytes'] = max(result['sampled_sum_rss_max_bytes'], current_rss)
                for n, proc, work in list(active):
                    if proc.poll() is not None:
                        result['worker_exit_codes'].append({'job': n, 'exit_code': proc.returncode})
                        if proc.returncode != 0:
                            raise RuntimeError(f'{mode} job{n} failed')
                        receipt = json.loads((work / 'receipt.json').read_text())
                        if receipt['status'] != 'completed' or receipt['denied_attempts'] != 0:
                            raise AssertionError('worker completed with rejected/failed activity')
                        if mode == 'api':
                            if reference is None:
                                reference = receipt['output']
                            if receipt['output'] != reference:
                                raise AssertionError('whole Gradio callback payload parity failed')
                        result['worker_receipts'].append(receipt)
                        active.remove((n, proc, work))
                if time.perf_counter() - started > (180 if mode == 'api' else 240):
                    raise TimeoutError('bounded batch timeout')
                time.sleep(0.02)
            result.update(status='completed', wall_seconds=time.perf_counter() - started)
            if mode == 'api':
                result['output_sha256'] = core.sha(core.canonical(reference))
            return result, reference
        except BaseException as exc:
            result.update(status='failed', error=repr(exc), seconds_until_failure=time.perf_counter() - started)
            raise
        finally:
            for proc in processes:
                reap(proc)
            result['all_final_exit_codes'] = [proc.returncode for proc in processes]
            core.save(folder / 'batch-receipt.json', result)


def workloads(environment_trial):
    source = identities()
    installed = json.loads((ROOT / 'install-results.json').read_text())
    selected = {r['profile']: r for r in installed['records'] if r['trial'] == environment_trial and r['exit_code'] == 0}
    if set(selected) != {'full', 'gradio'}:
        raise ValueError('explicit selected trial must be a complete successful pair')
    full = dict(selected['full']['installed_packages'])
    thin = dict(selected['gradio']['installed_packages'])
    if any(full.get(name) != version for name, version in thin.items()):
        raise ValueError('common dependency version drift')
    root = ROOT / 'workload-runs'
    root.mkdir(exist_ok=False)
    summary = {'source': source, 'selected_environment_trial': environment_trial, 'postrun_identity_verified': False,
               'limits': LIMITS, 'batches': [], 'original_tests': [], 'model_inference_performed': False,
               'browser_started': False, 'docker_started': False}
    reference = None
    for repetition in range(1, 4):
        for workers in (1, 2):
            order = ('full', 'gradio') if repetition % 2 else ('gradio', 'full')
            for profile in order:
                folder = root / f'r{repetition}-w{workers}-{profile}'
                verify_unchanged(source)
                try:
                    python = ROOT / 'install-trials' / f'{environment_trial}-{profile}/venv/bin/python'
                    row, reference = batch(folder, python, workers, reference)
                    verify_unchanged(source)
                    row.update(profile=profile, repetition=repetition, order=list(order))
                    summary['batches'].append(row)
                except BaseException as exc:
                    path = folder / 'batch-receipt.json'
                    failed = json.loads(path.read_text()) if path.exists() else {'status': 'not_started'}
                    failed.update(profile=profile, repetition=repetition, order=list(order), orchestration_error=repr(exc))
                    summary['batches'].append(failed)
                    raise
                finally:
                    core.save(ROOT / 'workload-results.json', summary)
    core.save(ROOT / 'output-reference.json', reference)
    # Original tests are a separate untimed acceptance stage; no extra packages installed.
    for profile in ('full', 'gradio'):
        verify_unchanged(source)
        folder = root / f'original-tests-{profile}'
        try:
            python = ROOT / 'install-trials' / f'{environment_trial}-{profile}/venv/bin/python'
            row, _ = batch(folder, python, 1, mode='original-tests')
            verify_unchanged(source)
            row['profile'] = profile
            summary['original_tests'].append(row)
        except BaseException as exc:
            path = folder / 'batch-receipt.json'
            failed = json.loads(path.read_text()) if path.exists() else {'status': 'not_started'}
            failed.update(profile=profile, orchestration_error=repr(exc))
            summary['original_tests'].append(failed)
            raise
        finally:
            core.save(ROOT / 'workload-results.json', summary)
    verify_unchanged(source)
    summary['postrun_identity_verified'] = True
    core.save(ROOT / 'workload-results.json', summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['install', 'workloads'])
    parser.add_argument('--exclusive-window', action='store_true')
    parser.add_argument('--environment-trial', type=int)
    args = parser.parse_args(argv)
    if not args.exclusive_window:
        parser.error('coordinator must release exclusive window first')
    if args.mode == 'install':
        if args.environment_trial is not None:
            parser.error('environment-trial applies only to workloads')
        install()
    else:
        if args.environment_trial not in (1, 2, 3):
            parser.error('explicit successful matched environment trial required')
        workloads(args.environment_trial)


if __name__ == '__main__':
    main()
