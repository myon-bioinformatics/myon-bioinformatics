"""One bounded, fresh-cache comparison for the approved Ironmate PPTX slice."""
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
    source = BASE / 'ironmate-inputs'
    prepared = Path((BASE / 'measurement-prepared.txt').read_text().strip())
    work = Path(tempfile.mkdtemp(prefix='measurement-', dir=BASE))
    (BASE / 'latest-measurement.txt').write_text(str(work) + '\n')
    npm = str(Path(shutil.which('npm')).resolve())
    env = {k:v for k,v in os.environ.items() if not k.lower().startswith('npm_config_')
           and k not in ('NODE_OPTIONS','NODE_PATH','NODE_ENV','PYTHONPATH','PYTHONHOME')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    report = {'source_commit': nano.COMMIT, 'os': platform.platform(),
              'python': platform.python_version(),
              'node': subprocess.check_output(['node','--version'], text=True).strip(),
              'npm': subprocess.check_output([npm,'--version'], text=True, env=env).strip(),
              'cache_policy': 'fresh empty npm cache per trial; no filesystem or remote CDN cache reset',
              'install_order': ['baseline','feature','feature','baseline','baseline','feature'],
              'scope': 'node_modules only; Python test tools and source excluded from install size/time',
              'installs': [], 'workloads': [], 'complete': False}


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
            project = root/'Ironmate'
            project.mkdir()
            snapshot = nano.read_snapshot(source)
            manifest = json.loads(snapshot['package.json'])
            lock = json.loads(snapshot['package-lock.json'])
            nano.validate_original(manifest, lock)
            for name,data in snapshot.items():
                target = project/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if kind == 'feature':
                for name in ('package.json','package-lock.json'):
                    shutil.copyfile(prepared/'Ironmate'/name,project/name)
                expected_manifest = json.loads(snapshot['package.json'])
                expected_manifest['dependencies'] = {'pptxgenjs':'4.0.1'}
                if not (json.loads((project/'package.json').read_bytes()) == expected_manifest):
                    raise AssertionError('changed prepared manifest')
                nano.validate_projection(lock, json.loads((project/'package-lock.json').read_bytes()))
            original = {name:(project/name).read_bytes() for name in ('package.json','package-lock.json')}
            local,args = settings(root)
            start = time.perf_counter()
            proc = subprocess.run([npm,'ci',*args],cwd=project,env=local,capture_output=True,text=True,timeout=180)
            seconds = time.perf_counter()-start
            (root/'npm.stdout').write_text(proc.stdout)
            (root/'npm.stderr').write_text(proc.stderr)
            item = {'index':index,'kind':kind,'seconds':seconds,'exit_code':proc.returncode,
                    'manifest_sha256':hashlib.sha256(original['package.json']).hexdigest(),
                    'lock_sha256':hashlib.sha256(original['package-lock.json']).hexdigest()}
            report['installs'].append(item)
            save()
            if proc.returncode:
                raise RuntimeError(f'npm ci failed: {root}/npm.stderr')
            if not (all((project/name).read_bytes()==data for name,data in original.items())):
                raise AssertionError('measure_node:105: validation failed')
            listing = subprocess.run([npm,'ls','--all','--parseable','--offline',*args],cwd=project,env=local,
                                     capture_output=True,text=True,timeout=30)
            (root/'npm-ls.stdout').write_text(listing.stdout)
            (root/'npm-ls.stderr').write_text(listing.stderr)
            if not (listing.returncode==0):
                raise AssertionError(listing.stderr)
            paths = set(line for line in listing.stdout.splitlines() if line)
            paths.discard(str(project))
            item.update(installed_package_paths=len(paths),node_modules=size(project/'node_modules'),cache=size(root/'cache'))
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
                    def run(worker):
                        worker_root=root/str(worker)
                        worker_root.mkdir()
                        tmp=worker_root/'tmp'
                        tmp.mkdir()
                        local=env|{'TMPDIR':str(tmp)}
                        start=time.perf_counter()
                        p=subprocess.run([sys.executable,'-S',str(BASE/'run_pptx_fixture.py'),
                                          str(projects[kind]),str(worker_root/'outputs')],
                                         cwd=worker_root,env=local,capture_output=True,text=True,timeout=120)
                        elapsed=time.perf_counter()-start
                        (worker_root/'stdout').write_text(p.stdout)
                        (worker_root/'stderr').write_text(p.stderr)
                        if p.returncode:
                            raise RuntimeError(f'workload failed: {worker_root}/stderr')
                        result=json.loads((worker_root/'outputs/result.json').read_text())
                        digests={name:result[name]['semantic_sha256'] for name in ('rich','reordered','image')}
                        return {'seconds':elapsed,'digests':digests,
                                'output_bytes':{name:result[name]['package_bytes'] for name in digests}}
                    start=time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                        workers=list(pool.map(run,range(2)))
                    batch=time.perf_counter()-start
                    for worker in workers:
                        if reference is None: reference=worker['digests']
                        if not (worker['digests']==reference):
                            raise AssertionError('independent ZIP/XML semantic mismatch')
                    report['workloads'].append({'kind':kind,'repeat':repeat,'concurrency':concurrency,
                                                'batch_seconds':batch,'workers':workers})
                    save()
        nano.read_snapshot(source)
        report['semantic_digests']=reference
        report['complete']=True
        save()
    except BaseException as exc:
        report['error']=str(exc)
        save()
        raise
    print(work)


if __name__ == '__main__':
    main()
