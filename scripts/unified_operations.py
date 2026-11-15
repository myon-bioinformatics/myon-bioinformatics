"""Read-only Pages view from the existing audit/evidence producers. Python 3.11+."""
from __future__ import annotations
import json
import datetime as dt
from pathlib import Path
from graphlib import TopologicalSorter, CycleError
from urllib.parse import quote


def collect_repository_counts(snapshot, fetch):
    """Count the REST inventories independently; partial reads stay unknown."""
    owner = snapshot.get('owner', 'myon-bioinformatics')
    integrations = {r['repository']: r for r in snapshot.get('integrations', [])}
    results = []
    for name in snapshot.get('repositories', []):
        root = f'/repos/{owner}/{name}'
        row = {'repository': name, 'open_issues_count': None, 'get_issues': None,
               'issues': None, 'pull_requests': None, 'complete': False,
               'checked': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
               'sources': []}
        results.append(row)
        if not integrations.get(name, {}).get('head_sha'):
            row['error'] = 'main SHA unavailable'
            continue
        try:
            metadata = fetch(root)
            inventories = {}
            for kind in ('issues', 'pulls'):
                items = []
                for page in range(1, 101):
                    path = f'{root}/{kind}?state=open&per_page=100&page={page}'
                    values = fetch(path)
                    if not isinstance(values, list):
                        raise ValueError('invalid REST inventory')
                    row['sources'].append('https://api.github.com' + path)
                    items.extend(values)
                    if len(values) < 100:
                        break
                else:
                    raise ValueError('REST inventory pagination incomplete')
                if len({v['number'] for v in items}) != len(items):
                    raise ValueError('REST inventory changed during pagination')
                inventories[kind] = items
            issues, pulls = inventories['issues'], inventories['pulls']
            row.update(open_issues_count=metadata['open_issues_count'], get_issues=len(issues),
                       issues=sum('pull_request' not in v for v in issues),
                       prs_in_issues=sum('pull_request' in v for v in issues),
                       pull_requests=len(pulls), complete=True)
            row['sources'].insert(0, 'https://api.github.com' + root)
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            row['error'] = type(exc).__name__
    return results

def _pages(fetch, path, key=None):
    """Bounded REST pagination. A partial observation must not appear complete."""
    rows = []
    for page in range(1, 11):
        value = fetch(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        items = value[key] if key else value
        if not isinstance(items, list):
            raise ValueError('invalid collection')
        rows.extend(items)
        if len(items) < 100:
            if key and value.get('total_count', len(rows)) != len(rows):
                raise ValueError('partial collection')
            return rows
    raise ValueError('collection exceeds 1000 items')



def refresh_evidence(evidence, snapshot, fetch):
    """Refresh current records and the named migration; preserve older SHA evidence."""
    active = {r['id'] for r in snapshot['records']}
    migration = {f"{r['repository']}#{r['number']}": r for r in (snapshot.get('pswb_migration') or {}).get('rows', [])}
    records = {r['id']: r for r in evidence['records']}
    for ident, row in migration.items():
        if row.get('complete') is False:
            continue
        records.setdefault(ident, dict(id=ident, repository=row['repository'], number=row['number'],
                                       kind='pr', url=row['url'], title=row.get('title'), events=[]))
        records[ident].update(head_sha=row.get('head_sha'), main_sha=row.get('main_sha'),
                             github_state='open' if row.get('open') else 'closed', merged=row.get('merged'),
                             metadata_checked=row.get('checked_at'))
    for ident, record in records.items():
        if ident not in active and ident not in migration:
            continue
        if migration.get(ident, {}).get('complete') is False:
            continue
        root = f"/repos/{snapshot['owner']}/{record['repository']}"
        number, at = record['number'], dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            comments = _pages(fetch, f'{root}/issues/{number}/comments')
            events = [{'type': 'comment', 'id': c['id'], 'actor': c['user']['login'], 'at': c['updated_at'],
                       'text': c.get('body'), 'url': c['html_url'], 'source': 'Pages current REST comments'} for c in comments]
            coverage = {'checked_at': at, 'complete': True, 'count': len(comments)}
            if record['kind'] == 'pr':
                reviews = _pages(fetch, f'{root}/pulls/{number}/reviews')
                inline = _pages(fetch, f'{root}/pulls/{number}/comments')
                events += [{'type': 'review', 'id': r['id'], 'actor': r['user']['login'], 'at': r['submitted_at'],
                            'review_sha': r['commit_id'], 'state': r['state'], 'text': r.get('body'),
                            'url': r['html_url'], 'source': 'Pages current REST reviews'} for r in reviews]
                events += [{'type': 'comment', 'id': c['id'], 'head_sha': c['commit_id'], 'actor': c['user']['login'],
                            'at': c['updated_at'], 'text': c.get('body'), 'url': c['html_url'],
                            'source': 'Pages current REST inline comments'} for c in inline]
                runs = _pages(fetch, f"{root}/actions/runs?head_sha={record['head_sha']}", 'workflow_runs')
                latest = {}
                for run in runs:
                    key = (run['workflow_id'], run['event'])
                    if key not in latest or (run['id'], run['run_attempt']) > (latest[key]['id'], latest[key]['run_attempt']):
                        latest[key] = run
                for run in latest.values():
                    events.append({'type': 'ci', 'name': run['name'], 'head_sha': run['head_sha'], 'run_id': run['id'],
                                   'attempt': run['run_attempt'], 'workflow_id': run['workflow_id'], 'event': run['event'],
                                   'status': run['status'], 'conclusion': run['conclusion'], 'at': run['updated_at'],
                                   'url': run['html_url'], 'source': 'Pages current REST workflow runs'})
                    for job in _pages(fetch, f"{root}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", 'jobs'):
                        events.append({'type': 'job', 'name': job['name'], 'head_sha': run['head_sha'], 'run_id': run['id'],
                                       'attempt': run['run_attempt'], 'job_id': job['id'], 'status': job['status'],
                                       'conclusion': job['conclusion'], 'at': job.get('completed_at') or job.get('started_at'),
                                       'url': job['html_url'], 'source': 'Pages current REST attempt jobs'})
                final = fetch(f'{root}/pulls/{number}')
                if final['head']['sha'] != record['head_sha'] or final['state'] != record.get('github_state', 'open'):
                    raise ValueError('head/state moved')
                record['review_coverage'] = dict(coverage, count=len(reviews), head_sha=record['head_sha'])
                record['current_collection'] = {'head_sha': record['head_sha'], 'checked_at': at,
                                                'attempt_scope': '現headのworkflow/event最新run・最新attempt',
                                                'jobs_scope': '取得attemptのjobs全ページ（最大1000件）'}
            # Replace the current comment/review set. Keep historical execution evidence.
            for event in record['events']:
                if event['type'] in ('ci', 'job'):
                    event['historical'] = True
            record['events'] = [e for e in record['events'] if e['type'] not in ('comment', 'review')] + events
            record['comment_coverage'] = coverage
            record.pop('collection_error', None)
        except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
            record['collection_error'] = type(exc).__name__
            record.pop('current_collection', None)
            record.pop('comment_coverage', None)
            record.pop('review_coverage', None)
    evidence['records'] = list(records.values())
    return evidence


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
            if event.get('type') == 'ci' and event.get('source') != 'Pages current REST workflow runs':
                event['historical'] = True
        for phase, observation in row.get('runs', {}).items():
            for run in observation.get('runs', []):
                if phase == 'head' and any(e.get('type') == 'ci' and not e.get('historical') and
                                          e.get('run_id') == run['id'] and e.get('attempt') == run['run_attempt'] for e in events):
                    continue
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
    counts = [dict(c, fresh=c.get('complete', False)) for c in evidence.get('repository_counts', [])]
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
            'catalog': snapshot.get('catalog_observation', {'status': '未取得'}),
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
    research = ui / 'research-audit.json'
    if research.exists():
        data['research'] = json.loads(research.read_text(encoding='utf-8'))
    content = template.read_text(encoding='utf-8')
    for token, name in [('CSS', 'unified.css'), ('MODEL', 'unified-model.js'), ('APP', 'unified-app.js')]:
        text = (ui / name).read_text(encoding='utf-8')
        if token == 'APP':
            research_script = ui / 'research-audit.js'
            content = content.replace('/*__RESEARCH__*/', research_script.read_text(encoding='utf-8') if research_script.exists() else '')
        if token == 'MODEL':
            text = (ui / 'shared-ui.js').read_text(encoding='utf-8') + '\n' + text
        content = content.replace('/*__' + token + '__*/', text)
    encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    content = content.replace('/*__DATA__*/', encoded)
    (Path(stage) / 'workspace.html').write_text(content, encoding='utf-8')
    (Path(stage) / 'unified-data.json').write_text(encoded, encoding='utf-8')
    return data


