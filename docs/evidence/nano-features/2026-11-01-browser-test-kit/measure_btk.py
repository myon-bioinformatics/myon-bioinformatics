"""One bounded, fresh-cache comparison for the approved browser-test-kit document-mirror slice."""
import concurrent.futures
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='explicitly permit the approved isolated installs')
    parser.add_argument('--exclusive-window', action='store_true', help='assert that other measurements are paused')
    args = parser.parse_args()
    if not args.execute or not args.exclusive_window:
        parser.error('both --execute and --exclusive-window are required')
    BASE = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('nano_node', BASE / 'prototype/tools/nano_node.py')
    nano = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nano)
    source = BASE / 'btk-inputs'
    prepared = Path((BASE / 'measurement-prepared.txt').read_text().strip())
    work = Path(tempfile.mkdtemp(prefix='measurement-', dir=BASE))
    (BASE / 'latest-measurement.txt').write_text(str(work) + '\n')
    npm = str(Path(shutil.which('npm')).resolve())
    env = {k:v for k,v in os.environ.items() if not k.lower().startswith('npm_config_')
           and k not in ('NODE_OPTIONS','NODE_PATH','NODE_ENV','PYTHONPATH','PYTHONHOME')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
    report = {'source_commit': nano.BTK_COMMIT, 'os': platform.platform(),
              'python': platform.python_version(),
              'node': subprocess.check_output(['node','--version'], text=True).strip(),
              'npm': subprocess.check_output([npm,'--version'], text=True, env=env).strip(),
              'cache_policy': 'fresh empty npm cache per trial; no filesystem or remote CDN cache reset',
              'install_order': ['baseline','feature','feature','baseline','baseline','feature'],
              'scope': 'node_modules only; preinstalled runtimes and source excluded from install size/time',
              'feature': nano.BTK_FEATURE, 'fixture_sha256': hashlib.sha256((BASE/'run_btk_fixture.py').read_bytes()).hexdigest(),
              'preparer_sha256': hashlib.sha256((BASE/'prototype/tools/nano_node.py').read_bytes()).hexdigest(),
              'browser_downloads_or_launches_permitted':False,
              'installs': [], 'workloads': [], 'complete': False}
    hardware = {'observation':'read before timed installs; not a resource allocation guarantee',
                'logical_cpus':os.cpu_count(), 'affinity_cpus':len(os.sched_getaffinity(0))}
    cpuinfo = Path('/proc/cpuinfo').read_text()
    hardware['cpu_models'] = sorted({line.split(':',1)[1].strip() for line in cpuinfo.splitlines() if line.startswith('model name')})
    hardware['mem_total'] = next((line.split(':',1)[1].strip() for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')),None)
    hardware['cgroup'] = {}
    for name in ('cpu.max','memory.max','memory.high','cpuset.cpus.effective'):
        try: hardware['cgroup'][name] = (Path('/sys/fs/cgroup')/name).read_text().strip()
        except OSError: hardware['cgroup'][name] = None
    report['hardware_before_run'] = hardware


    def save():
        (work / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')


    def size(path):
        regular = [p for p in path.rglob('*') if p.is_file() and not p.is_symlink()]
        return {'regular_file_count': len(regular),
                'apparent_regular_file_bytes': sum(p.stat().st_size for p in regular),
                'allocated_regular_file_bytes': sum(p.stat().st_blocks * 512 for p in regular)}


    def settings(root):
        for name in ('home','config','cache','tmp'):
            (root/name).mkdir()
        for name in ('user.npmrc','global.npmrc'):
            (root/name).write_text('')
        local = env | {'HOME':str(root/'home'),'XDG_CONFIG_HOME':str(root/'config'),'TMPDIR':str(root/'tmp')}
        args = ['--ignore-scripts','--no-audit','--no-fund','--no-update-notifier','--registry','https://registry.npmjs.org/',
                '--fetch-retries=0','--fetch-timeout=30000',
                '--userconfig',str(root/'user.npmrc'),'--globalconfig',str(root/'global.npmrc'),
                '--cache',str(root/'cache')]
        return local,args


    projects = {}
    try:
        for index, kind in enumerate(report['install_order'], 1):
            root = work / f'{index}-{kind}'
            root.mkdir()
            project = root/'browser-test-kit'
            project.mkdir()
            snapshot = nano.read_snapshot(source, blobs=nano.BTK_SOURCE_BLOBS)
            manifest = json.loads(snapshot['package.json'])
            lock = json.loads(snapshot['package-lock.json'])
            nano.validate_original(manifest, lock, feature=nano.BTK_FEATURE)
            for name,data in snapshot.items():
                target = project/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if kind == 'feature':
                for name in ('package.json','package-lock.json'):
                    shutil.copyfile(prepared/'browser-test-kit'/name,project/name)
                expected_manifest = json.loads(snapshot['package.json'])
                expected_manifest['devDependencies'] = nano.BTK_SELECTED
                assert json.loads((project/'package.json').read_bytes()) == expected_manifest, 'changed prepared manifest'
                nano.validate_projection(lock, json.loads((project/'package-lock.json').read_bytes()), feature=nano.BTK_FEATURE)
            original = {name:(project/name).read_bytes() for name in ('package.json','package-lock.json')}
            local,args = settings(root)
            item = {'index':index,'kind':kind,'status':'about_to_execute',
                    'manifest_sha256':hashlib.sha256(original['package.json']).hexdigest(),
                    'lock_sha256':hashlib.sha256(original['package-lock.json']).hexdigest()}
            report['installs'].append(item)
            save()
            start = time.perf_counter()
            try:
                proc = subprocess.run([npm,'ci',*args],cwd=project,env=local,capture_output=True,text=True,timeout=180)
                code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                code = 'timeout'
                stdout = exc.stdout.decode(errors='replace') if isinstance(exc.stdout,bytes) else (exc.stdout or '')
                stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr,bytes) else (exc.stderr or '')
            except OSError as exc:
                code, stdout, stderr = 'launch_error_nonexecuted', '', str(exc)
            seconds = time.perf_counter()-start
            (root/'npm.stdout').write_text(stdout)
            (root/'npm.stderr').write_text(stderr)
            item.update(seconds=seconds,exit_code=code,status='finished')
            save()
            if code != 0:
                raise RuntimeError(f'npm ci failed: {root}/npm.stderr')
            assert all((project/name).read_bytes()==data for name,data in original.items())
            listing = subprocess.run([npm,'ls','--all','--parseable','--offline',*args],cwd=project,env=local,
                                     capture_output=True,text=True,timeout=30)
            (root/'npm-ls.stdout').write_text(listing.stdout)
            (root/'npm-ls.stderr').write_text(listing.stderr)
            assert listing.returncode==0, listing.stderr
            paths = set(line for line in listing.stdout.splitlines() if line)
            paths.discard(str(project))
            relative_paths = {Path(path).relative_to(project).as_posix() for path in paths}
            expected_paths = set(json.loads((project/'package-lock.json').read_bytes())['packages']) - {''}
            assert relative_paths == expected_paths, 'installed package paths differ from reviewed lock'
            item.update(installed_package_paths=len(paths),node_modules=size(project/'node_modules'),cache=size(root/'cache'))
            item['installed_paths_match_lock'] = True
            projects[kind] = project
            save()
        # Validate same bounded cases; each process has an exclusive output/tmp tree.
        reference = None
        for concurrency in (1,2):
            for repeat in range(3):
                order = ('baseline','feature') if repeat%2==0 else ('feature','baseline')
                for kind in order:
                    root = work/f'workload-c{concurrency}-r{repeat}-{kind}'
                    root.mkdir()
                    batch_record = {'kind':kind,'repeat':repeat,'concurrency':concurrency,
                                    'fixed_jobs':2,'status':'about_to_execute'}
                    report['workloads'].append(batch_record)
                    save()
                    def run(worker):
                        worker_root=root/str(worker)
                        worker_root.mkdir()
                        tmp=worker_root/'tmp'
                        tmp.mkdir()
                        local=env|{'TMPDIR':str(tmp)}
                        start=time.perf_counter()
                        p=subprocess.run([sys.executable,'-S',str(BASE/'run_btk_fixture.py'),
                                          str(projects[kind]),str(worker_root/'outputs')],
                                         cwd=worker_root,env=local,capture_output=True,text=True,timeout=240)
                        elapsed=time.perf_counter()-start
                        (worker_root/'stdout').write_text(p.stdout)
                        (worker_root/'stderr').write_text(p.stderr)
                        if p.returncode:
                            raise RuntimeError(f'workload failed: {worker_root}/stderr')
                        result=json.loads((worker_root/'outputs/result.json').read_text())
                        digests={'full_parity_sha256': hashlib.sha256(json.dumps(result['parity'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()}
                        return {'seconds':elapsed,'digests':digests,
                                'result_json_bytes': (worker_root/'outputs/result.json').stat().st_size}
                    start=time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                        workers=list(pool.map(run,range(2)))
                    batch=time.perf_counter()-start
                    for worker in workers:
                        if reference is None: reference=worker['digests']
                        assert worker['digests']==reference, 'complete fact JSON/test-outcome parity mismatch'
                    batch_record.update(status='passed',batch_seconds=batch,workers=workers)
                    save()
        nano.read_snapshot(source, blobs=nano.BTK_SOURCE_BLOBS)
        report['semantic_digests']=reference
        report['complete']=True
        save()
    except BaseException as exc:
        report['error']=str(exc)
        if report['workloads'] and report['workloads'][-1]['status']=='about_to_execute':
            report['workloads'][-1]['status']='failed'
        save()
        raise
    print(work)


if __name__ == '__main__':
    main()
