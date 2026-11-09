#!/usr/bin/env python3
"""Publishable audit build, Python 3.11+ standard library only.

No writes to GitHub. On any incomplete observation the deployed site is retained.
"""
from __future__ import annotations
import argparse
import datetime as dt
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


def build(out, *, repos=None, previous=None, previous_history=None, source_root=ROOT):
    out = Path(out)
    before = previous_json(previous)
    if before is not None and before.get('schema') != 'parent-audit/1':
        raise ValueError('invalid previous audit schema')
    old_history = previous_json(previous_history)
    names = public_repositories(repos)
    snapshot = ci.audit(names, records_path=source_root / 'site/records.jsonl', previous=before, strict=True,
                        dependency_path=source_root / 'site/dependencies.json')
    ensure_complete(snapshot)
    snapshot['source'] = f'https://github.com/{ci.OWNER}/{ci.OWNER}'
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
            '<title>依存順序・固定SHA</title><body><a href="index.html">監査画面へ戻る</a>'
            + ci.render_dependency_report(snapshot) + '</body></html>', encoding='utf-8')
        for asset in (source_root / 'site/ui').iterdir():
            if asset.is_file():
                shutil.copyfile(asset, stage / asset.name)
        (stage / 'history.json').write_text(json.dumps(history, ensure_ascii=False) + '\n', encoding='utf-8')
        (stage / '.nojekyll').touch()
        progress = source_root / 'site/progress.json'
        (stage / 'progress.json').write_text(progress.read_text(encoding='utf-8') if progress.exists() else '{}\n', encoding='utf-8')
        with zipfile.ZipFile(stage / 'pages-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            # No tokens, credentials, local records or generated data in downloadable source.
            for path in sorted((source_root / 'site/ui').iterdir()):
                if path.is_file():
                    archive.write(path, f'site/ui/{path.name}')
            archive.write(source_root / 'tools/build_audit_pages.py', 'tools/build_audit_pages.py')
            archive.write(source_root / 'docs/audit-pages.md', 'docs/audit-pages.md')
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
    args = parser.parse_args(argv)
    try:
        snapshot = build(args.out, repos=args.repos.split(',') if args.repos else None,
                         previous=args.previous, previous_history=args.previous_history)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'audit build failed: {type(exc).__name__}; deployment retained', file=sys.stderr)
        return 2
    print(json.dumps({'generated_at': snapshot['generated_at'], 'summary': snapshot['summary'], 'output': args.out}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
