#!/usr/bin/env python3
"""Pinned, trusted offline workload comparison. No fetching during execution."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.constrained_lab_resources import read_cgroup, verify_budget

LANES = ('gh_identity', 'xprobe', 'Ironmate', 'mcp-toolcall-lab')
LIMIT = 65536


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def block_network():
    """Optional host libseccomp, loaded before trusted source; inherited by children.

    Deny socket creation and connection, including socketpair. No claim of a
    general sandbox (filesystem, inherited descriptors, escapes not controlled).
    """
    import ctypes
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('seccomp init failed')
    try:
        for name in (b'socket', b'socketpair', b'connect'):
            number = lib.seccomp_syscall_resolve_name(name)
            if number < 0 or lib.seccomp_rule_add(ctx, 0x00050001, number, 0) != 0:
                raise RuntimeError('seccomp rule failed')
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError('seccomp load failed')
    finally:
        lib.seccomp_release(ctx)
    import socket
    try:
        socket.socket()
    except PermissionError:
        return 'seccomp-deny-socket-socketpair-connect'
    raise RuntimeError('network denial self-check failed')


def exercise(lane, source, fixture, work):
    """Use independent simple oracles; never calibrate expected output from runs."""
    lines = fixture['lines']
    if lane == 'gh_identity':
        module = load(source / 'gh_identity.py', 'ghi')
        html = '<nav>ignored</nav><article id="main">' + ''.join('<p>'+s+'</p>' for s in lines) + '</article>'
        result = module.html_content(html, ['#main'])['body']
        expected = '\n\n'.join(lines)
    elif lane == 'xprobe':
        module = load(source / 'xprobe.py', 'xp')
        matches = module.search_text('\n'.join(lines), 'needle')
        result = len(matches)
        expected = sum(s.count('needle') for s in lines)
    elif lane == 'Ironmate':
        module = load(source / 'image_compare.py', 'im')
        n = fixture['image_side']
        left = (n, n, bytes([0, 0, 0, 255]) * (n*n))
        right = (n, n, bytes([10, 10, 10, 255]) * (n*n))
        result = module.metrics(left, right)
        expected = dict(rgb_mae=10.0, rgb_rmse=10.0, alpha_mae=0.0,
                        exact_rgba_pixels=0, pixels=n*n)
    else:
        module = load(source / 'src/mcp_toolcall_lab/jsonl_log_archive.py', 'arc')
        src, arc = work / 'input.jsonl', work / 'output.arc'
        records = [json.dumps({'id': i, 'text': line}, ensure_ascii=False) for i, line in enumerate(lines)]
        src.write_text('\n'.join(records)+'\n', encoding='utf-8')
        module.build_archive(src, arc)
        result = module.JsonlArchive(arc).search('needle')
        expected = [(i, line) for i, line in enumerate(records, 1) if 'needle' in line]
    if result != expected:
        raise ValueError('correctness oracle mismatch')
    return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def worker(lane, source, fixture_path, receipt, address_space_mib=128):
    # These per-process safeguards are NOT an aggregate 128MiB cgroup budget.
    resource.setrlimit(resource.RLIMIT_AS, (address_space_mib*1024*1024, address_space_mib*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    network = block_network()
    fixture = json.loads(Path(fixture_path).read_text())
    started, cpu = time.perf_counter(), time.process_time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = exercise(lane, Path(source), fixture, Path(tmp))
        report = {'correct': True, 'result_sha256': result}
    except Exception as exc:
        import traceback
        report = {'correct': False, 'error_type': type(exc).__name__,
                  'error_frames': [{'file': Path(f.filename).name, 'function': f.name,
                                    'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)]}
    report.update(wall_seconds=time.perf_counter()-started,
                  cpu_seconds=time.process_time()-cpu,
                  rss_peak_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                  address_space_limit_bytes=address_space_mib*1024*1024,
                  network=network, scope='worker-self; RSS includes interpreter startup; CPU/wall cover imports and workload')
    Path(receipt).write_text(json.dumps(report))
    return 0 if report['correct'] else 1


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def validate_sources(staged, manifest):
    for lane in LANES:
        source = staged / lane
        if git(source, 'rev-parse', 'HEAD') != manifest['sources'][lane]['sha']:
            raise ValueError('source SHA mismatch: '+lane)
        if git(source, 'status', '--porcelain', '--untracked-files=normal'):
            raise ValueError('source must be clean: '+lane)


def cgroup():
    try:
        return read_cgroup()
    except (OSError, ValueError, KeyError):
        return None


def wait_usage(proc, timeout):
    deadline = time.monotonic() + timeout
    while True:
        pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            return proc.returncode, usage
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(0.005)


def trial(lane, staged, fixture, traced, address_space_mib=128):
    with tempfile.TemporaryDirectory() as tmp:
        receipt = Path(tmp)/'receipt.json'
        cmd = [sys.executable, '-S', str(Path(__file__).resolve()), '--worker', lane,
               '--staged', str(staged), '--fixture', str(fixture), '--receipt', str(receipt),
               '--address-space-mib', str(address_space_mib)]
        started = time.perf_counter()
        # Drop caller credentials; preserve only deterministic runtime essentials.
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONHASHSEED': '7',
               'LANG': 'C.UTF-8', 'TZ': 'UTC', 'HOME': tmp}
        if traced:
            # Adapter inherits our sanitized environment via a fresh driver.
            driver = [sys.executable, '-S', str(ROOT/'tools/constrained_lab_exec_audit.py'),
                      '--timeout', '20', '--max-trace-bytes', str(LIMIT), '--', *cmd]
        else:
            driver = cmd
        try:
            with tempfile.TemporaryFile() as output:
                proc = subprocess.Popen(driver, env=env, stdin=subprocess.DEVNULL,
                                        stdout=output if traced else subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    rc, usage = wait_usage(proc, 25)
                except subprocess.TimeoutExpired:
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL)
                    _, usage = wait_usage(proc, 5)
                    rc = None
                output.seek(0)
                raw = output.read(LIMIT+1)
            observation = json.loads(raw) if traced and len(raw) <= LIMIT and raw else None
        except (OSError, ValueError):
            rc, observation, usage = None, None, None
        measured = json.loads(receipt.read_bytes()) if receipt.exists() and receipt.stat().st_size <= LIMIT else None
        return {'lane': lane, 'returncode': rc, 'worker': measured,
                'launch_wall_seconds': time.perf_counter()-started, 'audit': observation,
                'launch_cpu_seconds': usage.ru_utime + usage.ru_stime if usage else None,
                'launch_rss_max_descendant_bytes': usage.ru_maxrss*1024 if usage else None,
                'launch_usage_scope': 'wait4 driver plus reaped descendants; RSS is maximum, not sum',
                'correct': rc == 0 and measured is not None and measured['correct'],
                'external_total': None,
                'dependency_conclusion': 'unknown; retained attempts only' if traced else 'unobserved'}


def run(args):
    manifest = json.loads(Path(args.manifest).read_text())
    fixture = Path(args.fixture).resolve()
    staged = Path(args.staged).resolve()
    if digest(fixture) != manifest['fixture_sha256']:
        raise ValueError('fixture digest mismatch')
    validate_sources(staged, manifest)
    try:
        budget = verify_budget()
    except (OSError, ValueError) as exc:
        if not args.exploratory:
            raise ValueError('128MiB/CPU1 budget unavailable; use explicitly exploratory mode') from exc
        budget = None
    metadata = {'schema': 'constrained-real-workloads/1', 'manifest': manifest,
                'harness_sha256': digest(__file__), 'audit_sha256': digest(ROOT/'tools/constrained_lab_exec_audit.py'),
                'python': sys.version, 'python_executable': sys.executable, 'platform': platform.platform(),
                'kernel': platform.release(), 'cpu_affinity': sorted(os.sched_getaffinity(0)),
                'libc': platform.libc_ver(), 'gpu': 'not used; GPU device access is not isolated',
                'budget': budget, 'budget_verified': budget is not None,
                'experiment_class': 'exploratory' if args.exploratory else 'budget-verified',
                'worker_safeguards': {'address_space_bytes': args.address_space_mib*1024*1024, 'cpu_seconds': 10, 'file_bytes': LIMIT},
                'seed': 7, 'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'network': 'per-worker seccomp; fails before workload if unavailable',
                'measurement_scope': 'cgroup counters include unrelated processes and historical peak; not attributed to campaign',
                'complete_four_lane_demonstration': False,
                'campaign_definition': 'one task per lane, pooled across lanes; not intra-lane scaling',
                'unknown_metrics': ['aggregate workload memory peak', 'complete executable dependency total'],
                'runtime_environment': {'PYTHONHASHSEED': '7', 'LANG': 'C.UTF-8', 'TZ': 'UTC'},
                'no_speedup_claim': 'failed or unobserved pairs cannot establish overhead or speedup'}
    all_correct = True
    with open(args.output, 'x', encoding='utf-8') as output:
        output.write(json.dumps({'type': 'environment', **metadata}, sort_keys=True)+'\n')
        for repeat in range(args.repeats):
            for workers in (1, 2, 4):
                for traced in (False, True):
                    before, start = cgroup(), time.perf_counter()
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        rows = list(pool.map(lambda lane: trial(lane, staged, fixture, traced, args.address_space_mib), LANES))
                    after = cgroup()
                    all_correct = all_correct and all(r['correct'] for r in rows)
                    row = {'type': 'campaign', 'repeat': repeat, 'workers': workers, 'traced': traced,
                           'wall_seconds': time.perf_counter()-start, 'cgroup_before': before,
                           'cgroup_after': after, 'cgroup_cpu_delta_seconds':
                           (after['cpu_usage_usec']-before['cpu_usage_usec'])/1e6 if before and after else None,
                           'aggregate_workload_memory_peak': None, 'runs': rows}
                    output.write(json.dumps(row, sort_keys=True)+'\n'); output.flush()
    return 0 if all_correct else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--staged', required=True)
    p.add_argument('--manifest', default=str(ROOT/'fixtures/constrained-workloads/manifest.json'))
    p.add_argument('--fixture', default=str(ROOT/'fixtures/constrained-workloads/input.json'))
    p.add_argument('--output')
    p.add_argument('--repeats', type=int, choices=range(1, 6), default=3)
    p.add_argument('--exploratory', action='store_true')
    p.add_argument('--address-space-mib', type=int, choices=(128, 192, 256), default=128,
                   help='Additional per-process virtual-address guard; never replaces cgroup verification')
    p.add_argument('--worker', choices=LANES)
    p.add_argument('--receipt')
    a = p.parse_args()
    if a.worker:
        return worker(a.worker, Path(a.staged)/a.worker, a.fixture, a.receipt, a.address_space_mib)
    if not a.output:
        p.error('--output required')
    return run(a)

if __name__ == '__main__':
    sys.exit(main())
