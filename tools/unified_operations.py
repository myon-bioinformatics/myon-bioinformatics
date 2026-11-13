"""Read-only Pages view from the existing audit/evidence producers. Python 3.11+."""
from __future__ import annotations
import json
import datetime as dt
from pathlib import Path
from graphlib import TopologicalSorter, CycleError
from urllib.parse import quote


def collect_main_runs(snapshot, fetch):
    """Bounded read-only observations; old SHA and unavailable data stay explicit."""
    results, errors = [], []
    owner = snapshot.get('owner', 'myon-bioinformatics')
    for integration in snapshot.get('integrations', []):
        name, branch = integration['repository'], integration.get('branch')
        if not branch or not integration.get('head_sha'):
            errors.append({'repository': name, 'error': 'main SHA unavailable'})
            continue
        full = f'{owner}/{name}'
        try:
            page = fetch(f"/repos/{full}/actions/runs?branch={quote(branch, safe='')}&per_page=30")
            latest = {}
            for run in page['workflow_runs']:
                key = (run['workflow_id'], run['event'])
                old = latest.get(key)
                if old is None or (run['run_number'], run['run_attempt']) > (old['run_number'], old['run_attempt']):
                    latest[key] = run
            observed_sha = fetch(f"/repos/{full}/git/ref/heads/{quote(branch, safe='')}")['object']['sha']
            stable = observed_sha == integration['head_sha']
            for run in latest.values():
                row = {'repository': name, 'run_id': run['id'], 'attempt': run['run_attempt'],
                       'name': run['name'], 'head_sha': run['head_sha'], 'main_sha': integration['head_sha'],
                       'current_main': stable and run['head_sha'] == integration['head_sha'],
                       'main_stable': stable, 'conclusion': run['conclusion'], 'status': run['status'],
                       'url': run['html_url'], 'at': run['updated_at'], 'event': run['event'],
                       'checked': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                       'bot': any(s in (run.get('actor', {}).get('login', '')+' '+run['name']).lower()
                                  for s in ('dependabot', 'copilot')), 'jobs': []}
                if row['current_main'] and run['conclusion'] in ('failure', 'timed_out', 'action_required'):
                    try:
                        jobs = fetch(f"/repos/{full}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
                        row['jobs_complete'] = jobs.get('total_count', 0) <= len(jobs['jobs'])
                        row['jobs'] = [{'id': j['id'], 'name': j['name'], 'url': j['html_url'],
                                        'conclusion': j['conclusion'],
                                        'failed_steps': [s['name'] for s in j.get('steps', [])
                                                         if s.get('conclusion') == 'failure']}
                                       for j in jobs['jobs']]
                    except (ValueError, KeyError, RuntimeError, OSError) as exc:
                        row['jobs_error'] = type(exc).__name__
                results.append(row)
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            errors.append({'repository': name, 'error': type(exc).__name__})
    return {'runs': results, 'errors': errors, 'scope': '各repoの既定ブランチ直近30 run内、workflow/eventの最新。失敗jobは現mainの最新attemptのみ。ログ本文・必須チェックは未取得。'}


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
            'main_workflows': snapshot.get('main_workflows', {'runs': [], 'errors': [], 'scope': '未取得'}),
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
