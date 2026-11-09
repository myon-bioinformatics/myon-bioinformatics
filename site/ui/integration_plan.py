"""Python 3.11+ / stdlib only. Offline audit planning; no GitHub writes."""
import argparse
import json
from graphlib import TopologicalSorter, CycleError
from pathlib import Path


def calculate(data):
    rows = data.get('records', data.get('items', []))
    drafts = data.get('drafts', {})
    prs = [r for r in rows if r.get('kind') == 'pr' or r.get('type') == 'PR'] + data.get('dependency_tasks', [])
    by_id = {r.get('record_id') or r['id']: r for r in prs}
    aliases = {r.get('url', r['id']): k for k, r in by_id.items()}
    aliases.update({k:k for k in by_id})
    graph, missing = {}, {}
    completed = {k for k,r in by_id.items() if r.get('merged') is True}
    closed = {k for k,r in by_id.items() if r.get('github_state') == 'closed'} - completed
    for k,r in by_id.items():
        draft = drafts.get(r.get('url', r['id']), drafts.get(k, {}))
        deps = draft.get('dependencies', r.get('depends_on', r.get('dependencies', [])))
        if isinstance(deps, str): deps = deps.splitlines()
        deps = {aliases.get(d.strip(), d.strip()) for d in deps if d.strip()}
        for other,s in by_id.items():
            if other != k and other not in completed | closed and r.get('base_ref',r.get('base')) and s.get('head_ref') and r.get('repository',r.get('repo')) == s.get('repository',s.get('repo')) and r.get('base_ref',r.get('base')) == s.get('head_ref'):
                deps.add(other)
        if k in completed | closed: continue
        missing[k] = sorted(d for d in deps if d not in by_id or d in closed)
        graph[k] = deps - completed
    sorter = TopologicalSorter(graph)
    cycle = []
    try: sorter.prepare()
    except CycleError as exc: cycle = exc.args[1]
    order, waves = [], []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        if not ready: break
        waves.append([k for k in ready if k in graph])
        order.extend(k for k in ready if k in graph)
        sorter.done(*ready)
    unknown = {k for k,v in missing.items() if v}
    blocked = set(graph) - set(order)
    changed = True
    while changed:
        before = len(unknown)
        unknown.update(k for k,v in graph.items() if v & unknown)
        changed = len(unknown) != before
    return {'engine':'graphlib.TopologicalSorter','order':order,'waves':waves,'cycle':cycle,
            'actionable_order':[k for k in order if k not in unknown],
            'blocked':sorted(blocked),'missing':{k:v for k,v in missing.items() if v},
            'unsafe_missing':sorted(unknown),'waiting':{k:sorted(v) for k,v in graph.items() if v},
            'edges':{k:sorted(v) for k,v in graph.items()}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input');p.add_argument('--out',default='integration-result.json')
    a=p.parse_args();data=json.loads(Path(a.input).read_text())
    data['dependency_plan']=calculate(data)
    Path(a.out).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print('計算済み。循環:',bool(data['dependency_plan']['cycle']))

if __name__ == '__main__': main()
