"""Read-only Pages view from the existing audit/evidence producers. Python 3.11+."""
from __future__ import annotations
import json
from pathlib import Path
from graphlib import TopologicalSorter, CycleError


def view_data(snapshot, evidence, history, operations):
    records = evidence.get('records', [])
    current = {r['id'] for r in snapshot['records']}
    normalized = []
    for value in records:
        record = dict(value)
        record['inventory_absent'] = record['id'] not in current
        record['fresh'] = record['id'] in current
        normalized.append(record)
    active = [r for r in normalized if r.get('kind') == 'pr' and r.get('github_state') == 'open'
              and not r['inventory_absent']]
    graph = {r['id']: set(r.get('depends_on', [])) for r in active}
    for r in active:
        for other in active:
            if (r['id'] != other['id'] and r['repository'] == other['repository']
                    and r.get('base_ref') == other.get('head_ref')):
                graph[r['id']].add(other['id'])
    missing = sorted({d for values in graph.values() for d in values if d not in graph})
    try:
        order, cycle = list(TopologicalSorter(graph).static_order()), []
    except CycleError as exc:
        order, cycle = [], exc.args[1]
    counts = [dict(c, fresh=True) for c in evidence.get('repository_counts', [])]
    changes = [{'id': c['id'], 'change': key, 'before': value.get('before'), 'after': value.get('after')}
               for c in evidence.get('changes', []) for key, value in c.get('changes', {}).items()]
    parent = next((r for r in snapshot.get('integrations', [])
                   if r['repository'] == snapshot.get('owner')), {})
    return {'schema': 'integrated-operations/1', 'built_at': snapshot['generated_at'],
            'coverage': '🔄 ビルド時の監査値。⚠️ コメント・全attempt・必須CI：取得範囲のみ。',
            'records': normalized, 'counts': counts, 'changes': changes,
            'pins': snapshot.get('vendor_pins', []), 'conventions': snapshot.get('conventions', []),
            'fleet': snapshot.get('fleet', {}), 'history': history, 'operations': operations,
            'plan': {'order': [x for x in order if x in graph], 'cycle': cycle,
                     'missing': missing, 'edges': {k: sorted(v) for k, v in graph.items()}},
            'source': {'parent_main': parent.get('head_sha') or '', 'kind': 'Pages audit build'}}


def write_workspace(snapshot, evidence, history, stage, source_root):
    ui = Path(source_root) / 'site/ui'
    template = ui / 'unified.template.html'
    if not template.exists():
        return  # Older fixture/checkout: existing workspace remains intact.
    operations = json.loads((ui / 'operations-data.json').read_text(encoding='utf-8'))
    data = view_data(snapshot, evidence, history, operations)
    content = template.read_text(encoding='utf-8')
    for token, name in [('CSS', 'unified.css'), ('MODEL', 'unified-model.js'), ('APP', 'unified-app.js')]:
        text = (ui / name).read_text(encoding='utf-8')
        if token == 'MODEL':
            text = (ui / 'shared-ui.js').read_text(encoding='utf-8') + '\n' + text
        content = content.replace('/*__' + token + '__*/', text)
    encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    content = content.replace('/*__DATA__*/', encoded)
    (Path(stage) / 'workspace.html').write_text(content, encoding='utf-8')
    (Path(stage) / 'unified-data.json').write_text(encoded, encoding='utf-8')
    return data
