"""Regression checks for phase attribution and retained diagnostic evidence."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import constrained_lab_lzma_probe as p
from tools import constrained_lab_workloads as w

EVIDENCE = w.ROOT/'docs/evidence/constrained-workloads'


def read(name):
    return [json.loads(s) for s in (EVIDENCE/name).read_text().splitlines()]


class LzmaDiagnosisTests(unittest.TestCase):
    def test_initialization_failure_does_not_invent_compression_or_rss(self):
        with patch.object(p.lzma, 'LZMACompressor', side_effect=MemoryError), patch.object(p, 'snapshot', return_value={
                'virtual_bytes': None, 'rss_bytes': None, 'rss_high_water_bytes': None}):
            row = p.probe(0, 0, 'default', 'incremental')
        self.assertEqual(row['failed_phase'], 'initialization')
        self.assertEqual(row['error_type'], 'MemoryError')
        self.assertIsNone(row['roundtrip'])
        self.assertIsNone(row['peak_virtual_bytes'])
        self.assertEqual([s['phase'] for s in row['samples']], ['before', 'failed'])
        self.assertTrue(all(s['rss_bytes'] is None for s in row['samples']))

    def test_retained_matrix_separates_default_preset_and_empty_input(self):
        env, *rows = read('2026-10-28-lzma-probe.jsonl')
        self.assertEqual(env['probe_sha256'], hashlib.sha256(Path(p.__file__).read_bytes()).hexdigest())
        self.assertFalse(env['acceptance'])
        self.assertIsNone(env['cgroup_128mib_cpu1'])
        self.assertEqual(len(rows), 128)
        keyed = {(r['address_space_limit_bytes'], r['input_bytes'], r['preset'], r['mode']): r for r in rows}
        self.assertEqual(len(keyed), 128)
        for size in (0, 1, 1024, 1024**2):
            for preset in ('default', '6'):
                fail = keyed[128*1024**2, size, preset, 'incremental']
                self.assertEqual(fail['failed_phase'], 'initialization')
                self.assertEqual(fail['error_type'], 'MemoryError')
            for limit in (192*1024**2, 256*1024**2, None):
                controls = [keyed[limit, size, preset, mode] for preset in ('default', '6')
                            for mode in ('incremental', 'one_shot')]
                self.assertTrue(all(r['success'] and r['roundtrip'] for r in controls))
                self.assertEqual(len({r['output_sha256'] for r in controls}), 1)
            for preset in ('0', '3'):
                self.assertTrue(keyed[128*1024**2, size, preset, 'incremental']['roundtrip'])

    def test_retained_four_lanes_and_audit_are_independent(self):
        result_hashes = {lane: set() for lane in w.LANES}
        for limit in (128, 192, 256):
            env, *campaigns = read(f'2026-10-28-as{limit}.jsonl')
            self.assertEqual(env['harness_sha256'], w.digest(w.__file__))
            self.assertEqual(env['manifest'], json.loads((w.ROOT/'fixtures/constrained-workloads/manifest.json').read_text()))
            self.assertFalse(env['budget_verified'])
            self.assertFalse(env['complete_four_lane_demonstration'])
            self.assertEqual(env['worker_safeguards']['address_space_bytes'], limit*1024**2)
            self.assertEqual(len(campaigns), 6)
            for c in campaigns:
                self.assertIsNone(c['aggregate_workload_memory_peak'])
                self.assertEqual({r['lane'] for r in c['runs']}, set(w.LANES))
                for row in c['runs']:
                    self.assertIsNone(row['external_total'])
                    if c['traced']:
                        self.assertEqual(row['audit']['status'], 'tracing_error')
                        self.assertIsNone(row['worker'])
                        self.assertIsNone(row['audit']['external_count'])
                    elif limit == 128 and row['lane'] == 'mcp-toolcall-lab':
                        self.assertFalse(row['correct'])
                        self.assertEqual(row['worker']['error_type'], 'MemoryError')
                        self.assertEqual(row['worker']['error_frames'][-1]['function'], 'compress')
                        self.assertIn('_compress_block', [f['function'] for f in row['worker']['error_frames']])
                    else:
                        self.assertTrue(row['correct'])
                        self.assertGreater(row['worker']['rss_peak_bytes'], 0)
                        result_hashes[row['lane']].add(row['worker']['result_sha256'])
        self.assertTrue(all(len(hashes) == 1 for hashes in result_hashes.values()))


if __name__ == '__main__':
    unittest.main()
