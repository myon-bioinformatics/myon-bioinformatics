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
    parser = argparse.ArgumentParser(description='Offline-only continuation from the six completed exact npm installs')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--exclusive-window', action='store_true')
    parser.add_argument('--installed-trials', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or not args.exclusive_window:
        parser.error('explicit execution and exclusive window required')
    BASE = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('nano_node', BASE / 'prototype/tools/nano_node.py')
    nano = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nano)
    source = BASE / 'ironmate-inputs'
    prior = args.installed_trials.resolve()
    receipt_bytes = (prior/'receipt.json').read_bytes()
    prior_report = json.loads(receipt_bytes)
    assert len(prior_report['installs']) == 6 and all(r['exit_code']==0 for r in prior_report['installs'])
    work = Path(tempfile.mkdtemp(prefix='offline-fixed-alttext-', dir=prior))
    (BASE/'latest-offline.txt').write_text(str(work)+'\n')
    snapshot = nano.read_snapshot(source)
    original_manifest = json.loads(snapshot['package.json'])
    original_lock = json.loads(snapshot['package-lock.json'])
    nano.validate_original(original_manifest, original_lock)
    projects = {'baseline': prior/'5-baseline/Ironmate', 'feature': prior/'6-feature/Ironmate'}
    for kind, project in projects.items():
        manifest = json.loads((project/'package.json').read_bytes())
        lock = json.loads((project/'package-lock.json').read_bytes())
        if kind=='baseline':
            assert (project/'package.json').read_bytes()==snapshot['package.json']
            assert (project/'package-lock.json').read_bytes()==snapshot['package-lock.json']
        else:
            expected=json.loads(snapshot['package.json']); expected['dependencies']={'pptxgenjs':'4.0.1'}
            assert manifest==expected
            nano.validate_projection(original_lock, lock)
        for name, data in snapshot.items():
            if name not in ('package.json','package-lock.json'):
                assert (project/name).read_bytes()==data
        for path, package in lock['packages'].items():
            if path:
                installed=json.loads((project/path/'package.json').read_bytes())
                assert installed['version']==package['version'], (kind,path)
    env = {k:v for k,v in os.environ.items() if not k.lower().startswith('npm_config_')
           and k not in ('NODE_OPTIONS','NODE_PATH','NODE_ENV','PYTHONPATH','PYTHONHOME')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    report = {'source_commit': nano.COMMIT, 'prior_install_receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
              'fixture_sha256':hashlib.sha256((BASE/'run_pptx_fixture.py').read_bytes()).hexdigest(),
              'install_results_reused':True, 'network_performed':False,
              'initial_oracle_failure':'image input absolute path appeared in default alt text; now explicit fixed altText; no description normalization',
              'installs':prior_report['installs'], 'workloads':[], 'complete':False}
    def save():
        (work/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    try:
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
                        assert worker['digests']==reference, 'independent ZIP/XML semantic mismatch'
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
