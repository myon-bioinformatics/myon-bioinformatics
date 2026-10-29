#!/usr/bin/env python3
"""Disposable-process LZMA diagnostics; never a resource acceptance test."""
import argparse
import ctypes
import ctypes.util
import hashlib
import json
import lzma
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time


def snapshot():
    """Point-in-time virtual size/RSS; high-water RSS is separate, never VMS peak."""
    try:
        fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        values = {key: int(fields[key].split()[0])*1024 for key in ('VmSize', 'VmRSS')}
    except (OSError, KeyError, ValueError):
        values = {'VmSize': None, 'VmRSS': None}
    return {'virtual_bytes': values['VmSize'], 'rss_bytes': values['VmRSS'],
            'rss_high_water_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}


def encoder_estimates():
    # Same loaded system library interface as liblzma's xz easy encoder estimate.
    # These are estimates, NOT measured RSS or allocations.
    try:
        name = ctypes.util.find_library('lzma')
        lib = ctypes.CDLL(name)
        lib.lzma_easy_encoder_memusage.argtypes = [ctypes.c_uint32]
        lib.lzma_easy_encoder_memusage.restype = ctypes.c_uint64
        lib.lzma_version_string.restype = ctypes.c_char_p
        return {'library': name, 'version': lib.lzma_version_string().decode(),
                'estimated_encoder_bytes': {str(p): lib.lzma_easy_encoder_memusage(p) for p in (0, 3, 6)}}
    except (OSError, AttributeError, TypeError):
        return None


def cgroup_capability():
    root = Path('/sys/fs/cgroup')
    values = {}
    for key in ('memory.max', 'memory.current', 'memory.peak', 'cpu.max', 'memory.events'):
        try:
            values[key] = (root/key).read_text().strip()
        except OSError:
            values[key] = None
    # Attempt only an empty subgroup; never modify the enclosing cgroup.
    try:
        child = root/('lzma-probe-'+str(os.getpid()))
        child.mkdir()
        child.rmdir()
        values['subgroup_creation'] = 'available; limits not configured'
    except OSError as exc:
        values['subgroup_creation'] = {'error_type': type(exc).__name__, 'errno': exc.errno}
    values['scope'] = 'visible enclosing cgroup; shared counters, not attributable to probes'
    return values


def probe(limit_mib, size, preset, mode):
    if limit_mib:
        resource.setrlimit(resource.RLIMIT_AS, (limit_mib*1024**2,)*2)
    payload = b'x'*size
    kwargs = {} if preset == 'default' else {'preset': int(preset)}
    rows = []
    phase = 'initialization' if mode == 'incremental' else 'one_shot'
    started, cpu = time.perf_counter(), time.process_time()
    rows.append({'phase': 'before', **snapshot()})
    try:
        if mode == 'incremental':
            compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, **kwargs)
            rows.append({'phase': 'initialized', **snapshot()})
            phase = 'compress'
            output = compressor.compress(payload)
            rows.append({'phase': 'compressed', **snapshot()})
            phase = 'flush'
            output += compressor.flush()
        else:
            output = lzma.compress(payload, format=lzma.FORMAT_XZ, **kwargs)
        rows.append({'phase': 'finished', **snapshot()})
        result = {'success': True, 'output_sha256': hashlib.sha256(output).hexdigest(),
                  'roundtrip': lzma.decompress(output) == payload, 'failed_phase': None, 'error_type': None}
    except Exception as exc:
        rows.append({'phase': 'failed', **snapshot()})
        result = {'success': False, 'output_sha256': None, 'roundtrip': None,
                  'failed_phase': phase, 'error_type': type(exc).__name__}
    result.update(samples=rows, input_bytes=size, input_kind='repeated ASCII x', preset=preset,
                  mode=mode, address_space_limit_bytes=limit_mib*1024**2 if limit_mib else None,
                  effective_address_space_rlimit_bytes=[None if v == resource.RLIM_INFINITY else v
                                                       for v in resource.getrlimit(resource.RLIMIT_AS)],
                  wall_seconds=time.perf_counter()-started, cpu_seconds=time.process_time()-cpu,
                  peak_virtual_bytes=None,
                  scope='point samples; RSS high-water includes startup; CPU/wall include sampling and roundtrip; no aggregate peak')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--child', action='store_true')
    p.add_argument('--limit-mib', type=int, default=128)
    p.add_argument('--size', type=int, default=0)
    p.add_argument('--preset', default='default')
    p.add_argument('--mode', default='incremental')
    p.add_argument('--output')
    a = p.parse_args()
    if a.child:
        print(json.dumps(probe(a.limit_mib, a.size, a.preset, a.mode), sort_keys=True))
        return
    if not a.output:
        p.error('--output required')
    with open(a.output, 'x') as out:
        env = {'type': 'environment', 'schema': 'constrained-lzma-probe/1', 'python': sys.version,
               'python_executable': sys.executable, 'platform': platform.platform(),
               'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'liblzma': encoder_estimates(), 'acceptance': False,
               'visible_cgroup': cgroup_capability(),
               'cgroup_128mib_cpu1': None, 'aggregate_memory_peak': None,
               'cpu_affinity': sorted(os.sched_getaffinity(0)),
               'note': 'isolated children; RLIMIT_AS only; preset alternatives are controls, not archive changes'}
        out.write(json.dumps(env, sort_keys=True)+'\n')
        for limit in (128, 192, 256, 0):
            for size in (0, 1, 1024, 1024**2):
                for preset in ('default', '6', '0', '3'):
                    for mode in ('incremental', 'one_shot'):
                        cmd = [sys.executable, '-S', str(Path(__file__).resolve()), '--child',
                               '--limit-mib', str(limit), '--size', str(size), '--preset', preset, '--mode', mode]
                        child = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                               env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONHASHSEED': '7'})
                        row = json.loads(child.stdout) if child.returncode == 0 else {
                            'success': False, 'samples': None, 'error_type': 'child_failed',
                            'returncode': child.returncode, 'input_bytes': size, 'preset': preset,
                            'mode': mode, 'address_space_limit_bytes': limit*1024**2 if limit else None}
                        out.write(json.dumps({'type': 'probe', **row}, sort_keys=True)+'\n'); out.flush()


if __name__ == '__main__':
    main()
