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


def snapshot_comparison(snapshot, previous):
    """Compare consecutive published observations; absence is never a closure."""
    result = {'available': previous is not None, 'previous_at': (previous or {}).get('generated_at'),
              'current_at': snapshot['generated_at'], 'added': [], 'changed': [], 'absent': [],
              'counts': []}
    if previous is None:
        return result
    public = set(snapshot.get('repositories', []))
    if not public:  # A scoped fixture may only declare its records.
        public = {r['repository'] for r in snapshot['records'] if 'repository' in r}
    fields = ('title', 'head_sha', 'main_sha', 'base_sha', 'base_ref', 'draft', 'mergeable',
              'ci', 'github_state', 'review')
    def inventory(value):
        mains = {i['repository']: i.get('head_sha') for i in value.get('integrations', [])
                 if i.get('branch') == 'main'}
        return {r['id']: dict(r, github_state=r.get('github_state', 'open'),
                              main_sha=mains.get(r['repository']))
                for r in value['records'] if r.get('repository') in public}
    before, current = inventory(previous), inventory(snapshot)
    for key, record in current.items():
        identity = {k: record.get(k) for k in ('id', 'repository', 'number', 'kind', 'title', 'url')}
        if key not in before:
            result['added'].append(identity)
            continue
        changes = {f: {'before': before[key].get(f), 'after': record.get(f)}
                   for f in fields if before[key].get(f) != record.get(f)}
        if changes:
            result['changed'].append(dict(identity, changes=changes))
    result['absent'] = [{k: r.get(k) for k in ('id', 'repository', 'number', 'kind', 'title', 'url')}
                        for key, r in before.items() if key not in current]
    old_counts = {c['repository']: c for c in previous.get('repository_counts', [])}
    for c in snapshot.get('repository_counts', []):
        if c['repository'] not in public:
            continue
        old = old_counts.get(c['repository'], {})
        changes = {k: {'before': old.get(k), 'after': c.get(k)}
                   for k in ('open_issues_count', 'get_issues', 'issues', 'pull_requests')
                   if old.get(k) != c.get(k)}
        if changes:
            result['counts'].append({'repository': c['repository'], 'changes': changes})
    result['open_prs'] = {'before': sum(r.get('kind') == 'pr' for r in before.values()),
                         'after': sum(r.get('kind') == 'pr' for r in current.values())}
    return result


def view_data(snapshot, evidence, history, operations, previous=None):
    records = evidence.get('records', [])
    current = {r['id'] for r in snapshot['records']}
    normalized = []
    for value in records:
        record = dict(value)
        record['inventory_absent'] = record['id'] not in current
        record['fresh'] = record['id'] in current
        normalized.append(record)
    # Closed migration PRs belong to the same identity and timeline as the audit.
    # They remain visible without resurrecting absent open PRs in the active plan.
    migration = snapshot.get('pswb_migration')
    by_id = {r['id']: r for r in normalized}
    for row in (migration or {}).get('rows', []):
        key = f"{row['repository']}#{row['number']}"
        record = by_id.get(key)
        if record is None:
            record = {'id': key, 'repository': row['repository'], 'number': row['number'],
                      'kind': 'pr', 'title': row.get('title') or 'PSWB旧モジュール移行',
                      'url': row['url'], 'events': [], 'inventory_absent': True, 'fresh': False}
            normalized.append(record)
            by_id[key] = record
        record['migration'] = row
        if row.get('complete') is False:
            continue  # Failed refresh cannot promote partial metadata to current evidence.
        for field in ('head_sha', 'base_sha', 'main_sha', 'merged', 'merged_at', 'merge_sha', 'draft', 'mergeable'):
            if field in row:
                record[field] = row[field]
        if 'open' in row:
            record['github_state'] = 'open' if row['open'] else 'closed'
        record['metadata_checked'] = row['checked_at']
        events = record['events'] = [dict(e) for e in record['events']]
        for event in events:
            if event.get('type') == 'ci':
                event['historical'] = True
        for phase, observation in row.get('runs', {}).items():
            for run in observation.get('runs', []):
                events.append({'type': 'ci' if phase == 'head' else 'post_merge' if phase == 'merge' else 'main_ci',
                               'name': run['name'], 'head_sha': run['head_sha'], 'run_id': run['id'],
                               'attempt': run['run_attempt'], 'workflow_id': run['workflow_id'],
                               'event': run['event'], 'status': run['status'], 'conclusion': run['conclusion'],
                               'url': run['html_url'], 'at': None, 'observed_at': row['checked_at'],
                               'source': 'PSWB migration / exact SHA run observation'})
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
            'comparison': snapshot_comparison(snapshot, previous),
            'pins': snapshot.get('vendor_pins', []), 'conventions': snapshot.get('conventions', []),
            'fleet': snapshot.get('fleet', {}), 'history': history, 'operations': operations,
            'main_workflows': snapshot.get('main_workflows', {'runs': [], 'errors': [], 'scope': '未取得'}),
            'pswb_migration': migration,
            'plan': {'order': [x for x in order if x in graph], 'cycle': cycle,
                     'missing': missing, 'edges': {k: sorted(v) for k, v in graph.items()}},
            'source': {'parent_main': parent.get('head_sha') or '', 'kind': 'Pages audit build'}}


def write_workspace(snapshot, evidence, history, stage, source_root, previous=None):
    ui = Path(source_root) / 'site/ui'
    template = ui / 'unified.template.html'
    if not template.exists():
        return  # Older fixture/checkout: existing workspace remains intact.
    operations = json.loads((ui / 'operations-data.json').read_text(encoding='utf-8'))
    data = view_data(snapshot, evidence, history, operations, previous)
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

