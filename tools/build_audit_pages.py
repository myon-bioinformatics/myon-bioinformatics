#!/usr/bin/env python3
"""Publishable audit build, Python 3.11+ standard library only.

No writes to GitHub. On any incomplete observation the deployed site is retained.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import re
import datetime as dt
from html import escape
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ci_status as ci

MAX_BYTES = 32_000_000
BASE_URL = 'https://myon-bioinformatics.github.io/myon-bioinformatics'


# The observatory page is assembled here, not kept as a hand-written site/ui/index.html: every list below is written once.
# (view, icon, label in the top and footer rows, label in the side navigation)
VIEWS = (('all', '▤', '監査一覧', '監査ワークスペース'), ('focus', '◎', '優先対応', '優先対応'),
         ('dependencies', '⇄', '依存・統合順序・固定SHA', '依存・統合順序・固定SHA'), ('progress', '▦', '共通化の進捗', '共通化の進捗'),
         ('history', '◷', '週次記録', '週次スナップショット'), ('done', '✓', '完了記録', '完了記録'))
LINKS = (('workspace.html', '運用の確認・記録'), ('evidence.html', '◉ GitHub検証証跡 ↗'), ('component-ops/', '利用導線・責務移管 ↗'))  # other pages, before the actions
ACTIONS = (('refresh', '↻ 観測値を再読込', ''), ('import', '↑ JSON読込', ''), ('export', '↓ 記録を書出し', ''), ('snapshot', '＋ 週次記録', 'primary'))
# (id, aria-label, first option, further options): app.js fills repo and axis from the data.
FILTERS = (('repo', 'リポジトリ', '全リポジトリ', ()),
           ('ci', 'CI', 'すべてのCI', ('失敗', '成功', '実行中', '不明', '不完全', 'チェックなし', '対象外')),
           ('state', '作業状態', 'すべての状態', ('調査中', '着手中', '完了', '保留', 'ブロッカー')),
           ('axis', '監査軸', 'すべての監査軸', ()))
SCRIPTS = ('audit-model.js', 'scroll-controls.js', 'app.js', 'integration.js')
THEME = 'green'  # a PySaiyanWeBoo token theme (web_ui.theme_palettes()); style.css reads its --ui-* tokens


INTEGRATION_STATES = {'COMPLETE': '成功', 'FAILED': '失敗', 'RUNNING': '実行中', 'UNKNOWN': '取得不能', 'INCOMPLETE': '不完全', 'NO CHECKS': 'チェックなし'}


def integration_summary(rows):
    """The 統合後の確認 card: every value is an observation of this build, so it is written here and no script draws it."""
    def text(value):
        return escape('' if value is None else str(value), quote=True)

    def small(*values):
        return ''.join(f'<small>{text(value)}</small>' for value in values)

    def row(r):
        checks = ''.join(small(f"{c.get('name')}: {c.get('conclusion') or c.get('status')}") for c in r.get('checks') or [])
        references = ''.join('<p>%s: %s · %s%s</p>' % (text(x.get('kind')), text(x.get('consumer')), text(x.get('state')),
                                                       small(f"{x.get('sha')} / 観測元 {x.get('revision')}")) for x in r.get('references') or [])
        evidence = ''.join('<p>%s · %s%s</p>' % ('現在SHAの記録' if x.get('current') else '過去SHAの記録', text(x.get('sha')),
                                                small(f"{x.get('command')}: {x.get('result')}", x.get('source'), *x.get('facts', [])))
                           for x in r.get('evidence') or [])
        merged = ''.join('<p><a href="%s" target="_blank" rel="noopener">#%s %s</a>%s</p>' % (
            text(p.get('url')), text(p.get('number')), text(p.get('title')), small(f"{p.get('merged_at')} · {p.get('merge_commit_sha')}"))
            for p in r.get('merged') or [])
        return ('<tr><td>%s%s</td><td>%s%s</td><td>%s%s</td><td>%s%s<details><summary>参照・検証記録</summary>%s%s</details>'
                '<details><summary>直近のマージ %d件</summary>%s</details></td></tr>') % (
            text(r.get('repository')), small(f"{r.get('open_prs')}件"), text(r.get('branch') or '未取得'),
            small(r.get('head_sha') or '未取得', r.get('checked_at') or '未取得'), text(INTEGRATION_STATES.get(r.get('ci'), r.get('ci'))), checks,
            text(r.get('next')), small(*(r.get('explanations') or [])), references, evidence, len(r.get('merged') or []), merged)
    if not rows:
        return ''
    return ('<div class="card"><h3>統合後の確認</h3><p>オープンPRゼロと検証完了を分けて表示します。履歴は直近に更新されたclosed PR最大30件のうち'
            '既定ブランチにマージされたもの。実際のマージ順であり、依存関係ではありません。CI成功は観測したチェックの結果です。</p>'
            '<div class="table-wrap"><table><thead><tr><th>リポジトリ / open PR</th><th>既定ブランチ / SHA / 確認日時</th><th>CI</th>'
            '<th>次の作業 / マージ履歴</th></tr></thead><tbody>' + ''.join(row(r) for r in rows) + '</tbody></table></div></div>')


def observatory_page(snapshot=None, theme=THEME):
    """The observatory's page. What this build observed and no reader changes (the 統合後の確認 card) is written here from ``snapshot``;
    app.js fills #stats, #content and the repo/axis filters, which depend on the reader's own work records."""
    ui = ci._load_web_ui()
    heading = ui.page_heading("親子リポジトリ監査", eyebrow="監査", lead="PR・Issue・CI・依存関係を確認します。",
                              stamp={"label": "観測日時", "value": str((snapshot or {}).get("generated_at") or "未取得")})
    data_download = ui.download_json(snapshot or {}, id="audit-observation", filename="audit-observation.json", label="ビルド時の観測JSON ↓")
    integrations = integration_summary((snapshot or {}).get('integrations') or [])
    def views(side=False):
        return ''.join('<button data-view="%s"%s>%s%s</button>' % (view, ' class="active"' if index == 0 else '', icon, '　' + long if side else ' ' + short)
                       for index, (view, icon, short, long) in enumerate(VIEWS))

    def row(label):
        return f'<nav class="horizontal-row ui-shell-scroll" data-scroll-label="{label}" aria-label="{label}">{views()}</nav>'
    actions = ''.join('<a href="%s">%s</a>' % (href, escape(text)) for href, text in LINKS) + ''.join('<button%s id="%s">%s</button>' % (' class="%s"' % kind if kind else '', name, escape(text)) for name, text, kind in ACTIONS)
    side, top, bottom = views(side=True), row('上部ナビゲーション'), row('フッターナビゲーション')
    scripts = ''.join(f'<script src="{name}"></script>' for name in SCRIPTS if name != 'scroll-controls.js')
    scripts += '<script type="module" src="scroll-controls.js"></script><script type="module" src="pswb-records.js"></script>'
    theme = escape(theme, quote=True)
    filters = ''.join(f'<select id="{name}" aria-label="{label}"><option value="">{first}</option>'
                      + ''.join(f'<option>{option}</option>' for option in more) + '</select>' for name, label, first, more in FILTERS)
    return f'''<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Repository Observatory | 親子リポジトリ監査</title>
  <link rel="stylesheet" href="pswb-shell.css">
  <link rel="stylesheet" href="style.css">
</head>
<body data-ui-theme="{theme}">
<a class="skip-link" href="#content">監査一覧へ移動</a>
<aside>
  <div class="brand">◈ <span>OBSERVATORY</span></div>
  <div class="owner">{ci.OWNER}<br><small>REPOSITORY OPERATIONS</small></div>
  <nav aria-label="サイドナビゲーション">{side}</nav>
  <div class="aside-bottom"><span class="dot"></span> 静的 / GitHub Pages 対応<p>Python 3.11+ · 追加依存なし</p><a href="pages-source.zip">↓ Pages連携ソース</a></div>
</aside>
<main>
  <header class="workspace-header">
    <div class="header-meta"><span>WORKSPACE / WEEKLY AUDIT</span><a href="https://github.com/{ci.OWNER}/{ci.OWNER}" target="_blank" rel="noopener">親リポジトリ ↗</a></div>
    {top}
  </header>
  <section class="heading">
    {heading}
    <div class="actions horizontal-row ui-shell-scroll" data-scroll-label="監査の操作">{actions}{data_download}</div>
  </section>
  <div id="notice" role="status">データを読み込み中…</div>
  <section id="stats" class="stats"></section>
  <div class="tabs"><button id="listTab" class="active">監査一覧</button><button id="compare">選択項目を比較 <span id="count">0</span></button><span id="scope">全リポジトリ</span></div>
  <section id="filters"><input id="search" placeholder="⌕ リポジトリ、番号、課題を検索" aria-label="検索">{filters}<select id="sort" aria-label="並び順"><option value="priority">優先度順</option><option value="repo">リポジトリ順</option></select></section>
  <section id="integration-summary" aria-label="統合後の確認">{integrations}</section>
  <section id="content" tabindex="-1" aria-label="監査結果"></section>
  <footer>
    {bottom}
    <div class="footer-meta">OBSERVATORY <span>作業記録はこのブラウザに保存されます。端末間の引き継ぎにはJSON書出しを使用。</span></div>
  </footer>
</main>
<dialog id="detail"><div id="detailBody"></div><button id="close">閉じる</button></dialog>
<input type="file" id="file" accept="application/json,.json" hidden>
{scripts}
</body>
</html>
'''


def pswb_css():
    """PySaiyanWeBoo's tokens (the --ui-* palette of every token theme) and shell contract, from the reviewed vendor pin.
    Python remains the source of shared CSS; no npm package or CDN is required."""
    ui = ci._load_web_ui()
    return ''.join(ui.css_source(name) for name in ('tokens.css', 'shell.css', 'records.css', 'scroll.css'))


def previous_json(source):
    """Only 404 (first deployment) is optional; outages/malformed JSON stop publication."""
    if not source:
        return None
    if source.startswith('https://'):
        request = urllib.request.Request(source, headers={'Cache-Control': 'no-cache'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
    else:
        path = Path(source)
        if not path.exists():
            return None
        body = path.read_bytes()
    if len(body) > MAX_BYTES:
        raise ValueError('previous snapshot exceeds size limit')
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('previous snapshot must be an object')
    return value


def public_repositories(requested=None):
    # The shared helper explicitly filters visibility, private, owner and archive status.
    names = sorted(x['name'] for x in ci.ghi.owner_repositories(ci.OWNER, include_forks=True))
    if requested:
        # Do not log names outside the public inventory, including private repositories.
        if not set(requested).issubset(names):
            raise ValueError('requested scope includes an unavailable repository')
        return sorted(set(requested))
    if not names:
        raise ValueError('public repository inventory is empty')
    return names


def ensure_complete(snapshot):
    if snapshot.get('schema') != 'parent-audit/1' or not snapshot.get('repositories'):
        raise ValueError('invalid or empty audit scope')
    if snapshot.get('errors') or snapshot.get('truncated'):
        raise ValueError('incomplete audit; current deployment will be retained')
    if len({r['id'] for r in snapshot['records']}) != len(snapshot['records']):
        raise ValueError('duplicate audit identities')
    for r in snapshot['records']:
        if r['kind'] == 'pr' and (r['ci'] == 'UNKNOWN' or not r.get('review_complete')):
            raise ValueError('incomplete head/check/review observation')


def weekly_history(snapshot, previous):
    """Keep the first successful snapshot of each ISO week, for the latest 13 weeks."""
    if previous is not None and previous.get('schema') != 'parent-audit-history/1':
        raise ValueError('invalid weekly history schema')
    weeks = list((previous or {}).get('weeks', []))
    date = dt.datetime.fromisoformat(snapshot['generated_at']).astimezone(dt.timezone(dt.timedelta(hours=9)))
    iso = date.isocalendar()
    week = f'{iso.year}-W{iso.week:02d}'
    if not any(w['week'] == week for w in weeks):
        weeks.append({'week': week, 'at': snapshot['generated_at'], 'snapshot': snapshot})
    weeks.sort(key=lambda w: w['week'])
    return {'schema': 'parent-audit-history/1', 'weeks': weeks[-13:]}


def fleet_report(names, previous, *, source_root=ROOT, scoped=False, snapshot=None):
    """The vendor_fleet_plan report for the same public inventory (anonymous blob-less clones).

    snapshot: this build's parent-audit/1 snapshot, whose PR dependencies and CI per head join the evidence
    relations. Never blocks publication: a failed analysis is published as unavailable, so an old result is
    not shown as the current one.
    """
    import vendor_fleet_plan as fleet
    try:
        with tempfile.TemporaryDirectory(prefix='fleet-') as checkouts:
            analysed = fleet.Fleet(source_root, checkouts, fetch=True, repos=names if scoped else None)
            return fleet.report(analysed, previous=previous, listing=[{'name': n} for n in names], snapshot=snapshot)
    except Exception as exc:  # noqa: BLE001 - reported on the page, never a reason to retain stale observations
        return {'schema': fleet.REPORT_SCHEMA, 'status': 'unavailable', 'error': type(exc).__name__}


def split_fleet(report):
    """(snapshot section, knowledge graph): the graph with every edge goes to fleet.jsonld; data.json keeps
    rows and counts (facts are re-derivable from rows)."""
    if not report or 'edges' not in report.get('graph', {}):
        return report, None
    import vendor_fleet_plan as fleet
    graph = fleet.to_jsonld(report)
    section = {k: v for k, v in report.items() if k != 'facts'}
    section['graph'] = {k: v for k, v in report['graph'].items() if k != 'edges'} | {'edges_omitted': len(report['graph']['edges'])}
    evidence = report['graph'].get('evidence')
    if evidence:  # every relation is in fleet.jsonld; the page keeps counts, coverage and findings
        section['graph']['evidence'] = {k: v for k, v in evidence.items() if k != 'relations'} | {
            'relations_omitted': len(evidence.get('relations', []))}
    return section, graph


SCHEMA = 'github-evidence/1'


def _time(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return float('-inf')


def _checks(record, checks, sha, observed, *, phase='pre_merge'):
    for check in checks:
        url = check.get('url') or ''
        match = re.search(r'/runs/(\d+)(?:/job/(\d+))?', url)
        record['events'].append({
            'type': 'ci', 'at': None, 'observed_at': observed, 'head_sha': sha,
            'name': check.get('name'), 'status': check.get('status'),
            'conclusion': check.get('conclusion'), 'url': url,
            'run_id': match[1] if match else None,
            'job_id': match[2] if match else None, 'attempt': None,
            'phase': phase, 'source': 'Pages parent-audit/1 / executed checks; execution time not returned',
        })


def github_evidence(snapshot, captured=None):
    """Missing open-list entries never imply closure; base SHA never becomes main SHA."""
    if snapshot.get('schema') != 'parent-audit/1':
        raise ValueError('expected parent-audit/1')
    if captured and captured.get('schema') != SCHEMA:
        raise ValueError('invalid captured evidence schema')
    public = set(snapshot['repositories'])
    records = {}
    for value in (captured or {}).get('records', []):
        if value['repository'] in public:
            record = copy.deepcopy(value)
            record['inventory_presence'] = 'absent'
            records[record['id']] = record
    observed = snapshot['generated_at']
    for value in snapshot['records']:
        if value['repository'] not in public:
            continue
        record = records.setdefault(value['id'], {'id': value['id'], 'events': []})
        record['inventory_presence'] = 'present'
        at = value.get('metadata_checked') or value.get('checked') or observed
        if _time(at) < _time(record.get('metadata_checked')):
            continue
        for field in ('repository', 'number', 'kind', 'title', 'url', 'head_sha', 'base_sha',
                      'base_ref', 'head_ref', 'draft', 'mergeable', 'mergeable_state', 'labels',
                      'github_state', 'merged', 'merged_at', 'closed_at', 'merge_commit_sha'):
            if field in value:
                record[field] = value[field]
        record['metadata_checked'] = at
        record['events'].append({'type': 'status_sync', 'at': at, 'head_sha': record.get('head_sha'),
                                 'state': record.get('github_state'), 'url': record.get('url'),
                                 'source': 'Pages parent-audit/1 current observation'})
        if value.get('ci_head') and value['ci_head'] == value.get('head_sha'):
            for event in record['events']:
                if event['type'] in ('ci', 'job'):
                    event['historical'] = True
            _checks(record, value.get('checks', []), value['ci_head'],
                    value.get('workflow_checked') or at)
    integrations = {value['repository']: value for value in snapshot.get('integrations', [])}
    for record in records.values():
        integration = integrations.get(record['repository'])
        if not integration:
            continue
        if integration.get('branch') == 'main' and integration.get('head_sha'):
            record['main_sha'] = integration['head_sha']
            record['main_observed_at'] = integration.get('checked_at') or observed
            record['events'].append({'type': 'main_sync', 'at': record['main_observed_at'],
                                     'head_sha': record['main_sha'], 'source': 'Pages integration_status / main'})
        else:
            record['main_sha'] = None
            record['main_observed_at'] = observed
        merged = next((x for x in integration.get('merged', [])
                       if record['kind'] == 'pr' and x['number'] == record['number']), None)
        if merged:
            record.update(merged=True, github_state='closed', merged_at=merged['merged_at'],
                          closed_at=merged['merged_at'], merge_commit_sha=merged.get('merge_commit_sha'))
            record['events'].append({'type': 'closure', 'at': merged['merged_at'], 'merged': True,
                                     'url': merged['url'], 'source': 'Pages bounded merged-PR API history'})
            if (record.get('merge_commit_sha') == integration.get('head_sha')
                    and integration.get('ci') == 'COMPLETE'):
                record['events'].append({'type': 'post_merge', 'at': None,
                                         'observed_at': integration.get('checked_at') or observed,
                                         'head_sha': record['merge_commit_sha'], 'conclusion': 'success',
                                         'source': 'Pages exact merge-commit checks / required checks not assessed'})
                _checks(record, integration.get('checks', []), record['merge_commit_sha'],
                        integration.get('checked_at') or observed, phase='post_merge')
    counts = snapshot.get('repository_counts')
    if counts is None:
        counts = [{'repository': name, 'open_issues_count': None, 'get_issues': None,
                   'issues': sum(r['repository'] == name and r['kind'] == 'issue'
                                 and r.get('github_state', 'open') == 'open' for r in snapshot['records']),
                   'pull_requests': sum(r['repository'] == name and r['kind'] == 'pr'
                                        and r.get('github_state', 'open') == 'open' for r in snapshot['records']),
                   'checked': observed, 'sources': []} for name in sorted(public)]
    before = {r['id']: r for r in (captured or {}).get('records', [])}
    fields = ('head_sha', 'main_sha', 'github_state', 'merged', 'draft', 'mergeable')
    changes = []
    for key, record in records.items():
        values = {field: {'before': before.get(key, {}).get(field), 'after': record.get(field)}
                  for field in fields if before.get(key, {}).get(field) != record.get(field)}
        if values:
            changes.append({'id': key, 'changes': values})
    return {'schema': SCHEMA, 'built_at': observed, 'audit_observed_at': observed,
            'repository_counts': counts, 'changes': changes,
            'previous_at': (captured or {}).get('built_at'),
            'captured_at': (captured or {}).get('built_at'),
            'coverage': 'Pages公開時の監査＋保存済み証跡。コメント・レビュー・attempt履歴は取得済み範囲のみ。'
                        '一覧からの消失はclosureではありません。bot checksの除外など監査側の取得範囲も継承します。'
                        'required checksの照合、マージ実行、GitHubへの書込みは行いません。',
            'records': list(records.values())}


def write_github_evidence(snapshot, stage, *, source_root):
    source = Path(source_root) / 'site/evidence/observations.json.gz'
    captured = json.loads(gzip.decompress(source.read_bytes())) if source.exists() else None
    result = github_evidence(snapshot, captured)
    (Path(stage) / 'evidence-data.json').write_text(
        json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    return result


def build(out, *, repos=None, previous=None, previous_history=None, source_root=ROOT, fleet=None):
    out = Path(out)
    before = previous_json(previous)
    if before is not None and before.get('schema') != 'parent-audit/1':
        raise ValueError('invalid previous audit schema')
    old_history = previous_json(previous_history)
    names = public_repositories(repos)
    snapshot = ci.audit(names, records_path=source_root / 'site/records.jsonl', previous=before, strict=True,
                        dependency_path=source_root / 'site/dependencies.json')
    ensure_complete(snapshot)
    snapshot['integrations'] = []
    for name in names:
        count = sum(r['kind'] == 'pr' and r['repository'] == name for r in snapshot['records'])
        try:
            snapshot['integrations'].append(ci.integration_status(name, count))
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            snapshot['integrations'].append({'repository': name, 'open_prs': count, 'ci': 'UNKNOWN',
                                             'error': type(exc).__name__, 'next': '既定ブランチの観測を再試行'})

    # Bind the registry to the observed parent SHA, never a mutable local/PR copy.
    parent = next((r for r in snapshot['integrations'] if r['repository'] == ci.OWNER), None)
    parent_heads = {}
    if parent and parent.get('head_sha'):
        try:
            import base64
            content = ci.get(f"/repos/{ci.OWNER}/{ci.OWNER}/contents/child-heads.json?ref={parent['head_sha']}")
            parent_heads = json.loads(base64.b64decode(content['content']))['heads']
        except (ValueError, KeyError, RuntimeError, OSError):
            parent_heads = {}
    evidence_path = source_root / 'site/integration-evidence.json'
    evidence = json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path.exists() else {}
    if not isinstance(evidence, dict):
        raise ValueError('integration evidence must be an object')
    ci.explain_integrations(snapshot['integrations'], snapshot.get('vendor_pins', []), parent_heads, evidence)
    snapshot['source'] = f'https://github.com/{ci.OWNER}/{ci.OWNER}'
    fleet_graph = None
    if fleet is not None:
        produce = fleet if getattr(fleet, 'takes_snapshot', False) else (lambda names, before, snapshot=None: fleet(names, before))
        snapshot['fleet'], fleet_graph = split_fleet(produce(names, (before or {}).get('fleet'), snapshot=snapshot))
    history = weekly_history(snapshot, old_history)
    # Build away from the destination. No partial generation overwrites a good local output.
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='audit-build-', dir=out.parent) as directory:
        stage = Path(directory) / 'dist'
        ci.write_audit_site(snapshot, stage)
        (stage / 'index.html').rename(stage / 'report.html')
        (stage / 'dependencies.html').write_text(
            '<!doctype html><html lang="ja"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="0;url=index.html#dependencies">'
            '<title>依存・統合順序</title><a href="index.html#dependencies">依存・統合順序を開く</a></html>',
            encoding='utf-8')
        for asset in (source_root / 'site/ui').iterdir():
            if asset.is_file():
                shutil.copyfile(asset, stage / asset.name)
        evidence = write_github_evidence(snapshot, stage, source_root=source_root)
        from tools.unified_operations import write_workspace
        write_workspace(snapshot, evidence, history, stage, source_root)
        # Publish the standalone operations guide alongside the audit workspace.
        operations = source_root / 'site/component-ops'
        if operations.is_dir():
            shutil.copytree(operations, stage / 'component-ops')
        (stage / 'index.html').write_text(observatory_page(snapshot), encoding='utf-8')
        (stage / 'pswb-shell.css').write_text(pswb_css(), encoding='utf-8')
        ui = ci._load_web_ui()
        for name in ('scroll.js', 'records.js'):
            (stage / ('pswb-' + name)).write_text(ui.js_source(name), encoding='utf-8')
        (stage / 'history.json').write_text(json.dumps(history, ensure_ascii=False) + '\n', encoding='utf-8')
        if fleet_graph is not None:  # the whole graph as JSON-LD for agents and other tools
            (stage / 'fleet.jsonld').write_text(json.dumps(fleet_graph, ensure_ascii=False) + '\n', encoding='utf-8')
        (stage / '.nojekyll').touch()
        progress = source_root / 'site/progress.json'
        (stage / 'progress.json').write_text(progress.read_text(encoding='utf-8') if progress.exists() else '{}\n', encoding='utf-8')
        with zipfile.ZipFile(stage / 'pages-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            # No tokens, credentials, local records or generated data in downloadable source.
            for path in sorted((source_root / 'site/ui').iterdir()):
                if path.is_file():
                    archive.write(path, f'site/ui/{path.name}')
            if operations.is_dir():
                for path in sorted(operations.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(source_root))
            for name in ('pswb-shell.css', 'pswb-scroll.js', 'pswb-records.js'):
                archive.write(stage / name, 'site/ui/' + name)
            archive.write(source_root / 'tools/build_audit_pages.py', 'tools/build_audit_pages.py')
            unified = source_root / 'tools/unified_operations.py'
            if unified.exists():
                archive.write(unified, 'tools/unified_operations.py')
            archive.write(source_root / 'docs/audit-pages.md', 'docs/audit-pages.md')
            captured = source_root / 'site/evidence/observations.json.gz'
            if captured.exists():
                archive.write(captured, 'site/evidence/observations.json.gz')
        # Publication is a single Pages artifact; all validation has finished before copying it.
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(stage, out)
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='site/dist')
    parser.add_argument('--repos', help='Comma-separated subset of public inventory for local checks')
    parser.add_argument('--previous', default=BASE_URL + '/data.json')
    parser.add_argument('--previous-history', default=BASE_URL + '/history.json')
    parser.add_argument('--no-fleet', action='store_true', help='skip the vendor_fleet_plan dependency/update section')
    args = parser.parse_args(argv)
    scoped = bool(args.repos)

    def produce(names, before, snapshot=None):
        return fleet_report(names, before, scoped=scoped, snapshot=snapshot)
    produce.takes_snapshot = True  # build() passes this build's audit snapshot (PR dependencies, CI per head)
    try:
        snapshot = build(args.out, repos=args.repos.split(',') if args.repos else None,
                         previous=args.previous, previous_history=args.previous_history,
                         fleet=None if args.no_fleet else produce)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'audit build failed: {type(exc).__name__}; deployment retained', file=sys.stderr)
        return 2
    print(json.dumps({'generated_at': snapshot['generated_at'], 'summary': snapshot['summary'], 'output': args.out}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

