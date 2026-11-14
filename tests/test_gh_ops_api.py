"""gh_ops API-side commands: fleet-apply, fleet-issues, PR body edits, records-import, variables and comments, and the write-file list."""

import base64
import copy
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from test_gh_ops import REPO as REPO_gh_ops_pr_edit_body, client_for, gh_ops, pr_payload, reply


# ═══ · gh ops fleet apply ════════════════════════════════════════════════════════════════════════════════
# Stateful offline API: coordinated commits, PR reuse and source closure ordering.

class FleetRemote:
    def __init__(self):
        self.repos = {}
        self.calls = []
        self.seq = 10
        self.hook = None
        self.conflict = set()
        self.refusal = set()
        self.create_timeout = set()
        self.comment_read_error = set()
        self.close_error = set()

    def sha(self):
        self.seq += 1
        return f'{self.seq:040x}'

    def add(self, repo, *, target=True, sources=()):
        tree = self.sha()
        main, work = self.sha(), self.sha()
        r = {'refs': {'main': main, 'work': work}, 'trees': {tree: {}},
             'commits': {main: {'tree': tree, 'parents': []}, work: {'tree': tree, 'parents': [main]}},
             'prs': {}, 'comments': {}, 'created': 0}
        if target:
            r['prs'][149] = {'number': 149, 'branch': 'work', 'state': 'open', 'title': 'Existing', 'body': 'Keep this'}
        for n in sources:
            sha = self.sha()
            r['refs'][f'source-{n}'] = sha
            r['commits'][sha] = {'tree': tree, 'parents': [main]}
            r['prs'][n] = {'number': n, 'branch': f'source-{n}', 'state': 'open', 'title': f'Source {n}', 'body': 'source'}
        self.repos[repo] = r
        return r

    def ancestors(self, r, sha):
        todo, seen = [sha], set()
        while todo:
            sha = todo.pop()
            if sha not in seen:
                seen.add(sha); todo.extend(r['commits'][sha]['parents'])
        return seen

    def pr(self, repo, n):
        r = self.repos[repo]; p = r['prs'][n]
        return {'number': n, 'state': p['state'], 'title': p['title'], 'body': p['body'], 'merged': False,
                'head': {'ref': p['branch'], 'sha': r['refs'][p['branch']], 'repo': {'full_name': repo}},
                'base': {'ref': 'main', 'repo': {'full_name': repo}},
                'html_url': f'https://github.com/{repo}/pull/{n}'}

    def __call__(self, method, url, body, headers):
        u = urllib.parse.urlsplit(url); parts = urllib.parse.unquote(u.path).split('/')
        repo = '/'.join(parts[2:4]); path = '/'.join(parts[4:]); query = urllib.parse.parse_qs(u.query)
        self.calls.append((repo, method, path, copy.deepcopy(body)))
        if self.hook:
            self.hook(repo, method, path, body)
        r = self.repos[repo]
        reply = lambda data=None, status=200: gh_ops.Response(status, copy.deepcopy(data), {})
        if path == '':
            return reply({'default_branch': 'main'})
        if path == 'pulls' and method == 'GET':
            prs = [self.pr(repo, n) for n in r['prs']]
            prs = [p for p in prs if p['state'] == query.get('state', ['open'])[0]]
            if 'head' in query:
                prs = [p for p in prs if query['head'][0] == repo.split('/')[0] + ':' + p['head']['ref']]
            return reply(prs)
        if path == 'pulls' and method == 'POST':
            n = max([148, *r['prs']]) + 1
            r['prs'][n] = {'number': n, 'branch': body['head'], 'state': 'open', 'title': body['title'], 'body': body['body']}
            r['created'] += 1
            if repo in self.create_timeout:
                self.create_timeout.remove(repo)
                raise gh_ops.GhOpsError('lost creation response')
            return reply(self.pr(repo, n), 201)
        if path.startswith('pulls/'):
            n = int(path.split('/')[1])
            if method == 'PATCH':
                if body.get('state') == 'closed' and (repo, n) in self.close_error:
                    return reply({}, 503)
                r['prs'][n].update(body)
            return reply(self.pr(repo, n))
        if path.startswith('git/ref/heads/'):
            branch = path[len('git/ref/heads/'):]
            return reply({'object': {'sha': r['refs'][branch]}}) if branch in r['refs'] else reply({}, 404)
        if path == 'git/refs':
            branch = body['ref'][len('refs/heads/'):]
            if branch in r['refs']:
                return reply({}, 422)
            r['refs'][branch] = body['sha']
            return reply({'object': {'sha': body['sha']}}, 201)
        if path.startswith('git/refs/heads/'):
            if repo in self.refusal:
                return reply({}, 422)
            branch = path[len('git/refs/heads/'):]
            assert body['force'] is False
            assert r['refs'][branch] in self.ancestors(r, body['sha'])
            r['refs'][branch] = body['sha']
            return reply({'object': {'sha': body['sha']}})
        if path.startswith('git/commits/'):
            c = r['commits'][path.split('/')[-1]]
            return reply({'tree': {'sha': c['tree']}})
        if path == 'git/commits':
            sha = self.sha()
            r['commits'][sha] = {'tree': body['tree'], 'parents': body['parents']}
            return reply({'sha': sha}, 201)
        if path.startswith('git/trees/'):
            tree = r['trees'][path.split('/')[-1]]
            return reply({'truncated': False, 'tree': [{'path': p, 'type': 'blob', 'mode': '100644', 'sha': s} for p, s in tree.items()]})
        if path == 'git/trees':
            tree = dict(r['trees'][body['base_tree']])
            for e in body['tree']:
                if e['sha'] is None:
                    tree.pop(e['path'], None)
                else:
                    tree[e['path']] = e['sha']
            sha = self.sha(); r['trees'][sha] = tree
            return reply({'sha': sha}, 201)
        if path == 'git/blobs':
            return reply({'sha': gh_ops._git_blob_sha(base64.b64decode(body['content']))}, 201)
        if path.startswith('compare/'):
            source, target = path[len('compare/'):].split('...')
            contains = source in self.ancestors(r, target)
            return reply({'status': 'ahead' if contains else 'diverged', 'base_commit': {'sha': source},
                          'merge_base_commit': {'sha': source if contains else '0' * 40}})
        if path == 'merges':
            if repo in self.conflict:
                return reply({'message': 'Merge conflict'}, 409)
            sha = self.sha(); head = r['refs'][body['base']]
            r['commits'][sha] = {'tree': r['commits'][head]['tree'], 'parents': [head, body['head']]}
            r['refs'][body['base']] = sha
            return reply({'sha': sha}, 201)
        if path.startswith('issues/') and path.endswith('/comments'):
            n = int(path.split('/')[1])
            if method == 'GET':
                return reply(r['comments'].get(n, []))
            self.sha()
            c = {'id': self.seq + 10000, 'body': body['body'],
                 'issue_url': f'https://api.github.com/repos/{repo}/issues/{n}',
                 'html_url': f'https://github.com/{repo}/pull/{n}#issuecomment-{self.seq + 10000}'}
            r['comments'].setdefault(n, []).append(c)
            return reply(c, 201)
        if path.startswith('issues/comments/'):
            if repo in self.comment_read_error:
                return reply({}, 503)
            return reply(next(c for cs in r['comments'].values() for c in cs if c['id'] == int(path.split('/')[-1])))
        raise AssertionError((repo, method, path, body))

    def client(self):
        return gh_ops.Client(token='test-token', transport=self)


def manifest(*rows):
    return {'schema': 'gh-ops-fleet/1', 'repositories': list(rows)}


def row(repo='o/a', **kwargs):
    return {'repository': repo, 'target_pr': 149, 'sources': [150, 151],
            'files': {'a.txt': {'text': '日本語\n'}}, **kwargs}


def writes(remote):
    return [(repo, method, path, body) for repo, method, path, body in remote.calls if method != 'GET']


def test_batch_groups_files_reuses_one_pr_and_closes_only_after_verified_changes():
    remote = FleetRemote()
    a = remote.add('o/a', sources=[150, 151]); remote.add('o/b', sources=[150, 151])
    data = manifest(row(), row(files={'b.txt': {'text': 'second'}}), row('o/b'))
    plan = gh_ops.fleet_apply(data, client=remote.client())
    assert plan['ok'] and plan['total'] == 2 and not writes(remote)
    assert plan['repositories'][0]['files'] == ['a.txt', 'b.txt']
    result = gh_ops.fleet_apply(data, write=True, client=remote.client(), jobs=2)
    assert result['ok'] and result['succeeded'] == 2
    assert all(p['closed'] == [150, 151] for p in result['repositories'])
    assert a['created'] == 0 and a['prs'][149]['state'] == 'open'
    assert a['prs'][149]['body'].startswith('Keep this\n\n## Consolidated scope')
    assert sum(repo == 'o/a' and path == 'git/commits' for repo, _, path, _ in writes(remote)) == 1
    assert set(a['trees'][a['commits'][a['refs']['work']]['tree']]) == {'a.txt', 'b.txt'}
    mutations = [(path, body) for repo, _, path, body in writes(remote) if repo == 'o/a']
    commit_index = next(i for i, (p, _) in enumerate(mutations) if p == 'git/commits')
    close_index = next(i for i, (p, b) in enumerate(mutations) if p == 'pulls/150' and b.get('state') == 'closed')
    assert close_index > commit_index
    remote.calls.clear()
    retry = gh_ops.fleet_apply(data, write=True, client=remote.client())
    assert retry['ok'] and not writes(remote)  # no duplicate merges, commits, PRs, comments or closure


def test_create_branch_and_one_pr_then_reuse_on_retry():
    remote = FleetRemote(); r = remote.add('o/a', target=False)
    data = manifest({'repository': 'o/a', 'branch': 'batch/new', 'title': 'Batch change',
                     'files': {'a': {'text': 'new'}}, 'append_body': 'Verified locally'})
    result = gh_ops.fleet_apply(data, write=True, client=remote.client())
    assert result['ok'] and r['created'] == 1
    assert len(r['prs']) == 1 and r['prs'][149]['branch'] == 'batch/new'
    remote.calls.clear()
    assert gh_ops.fleet_apply(data, write=True, client=remote.client())['ok']
    assert r['created'] == 1 and not writes(remote)


def test_existing_pr_selected_without_number():
    remote = FleetRemote(); remote.add('o/a')
    result = gh_ops.fleet_apply(manifest({'repository': 'o/a', 'files': {'a': {'text': 'a'}}}),
                                write=True, client=remote.client())
    assert result['ok'] and result['repositories'][0]['target_pr'] == 149
    assert remote.repos['o/a']['created'] == 0


def test_multiple_prs_require_explicit_target_without_mutation():
    remote = FleetRemote(); remote.add('o/a', sources=[150])
    result = gh_ops.fleet_apply(manifest({'repository': 'o/a', 'branch': 'new', 'title': 'x'}),
                                write=True, client=remote.client())
    assert not result['ok'] and result['repositories'][0]['status'] == 'blocked'
    assert 'multiple open PRs' in result['repositories'][0]['reason'] and not writes(remote)


def test_conflict_leaves_sources_open_and_other_repo_completes():
    remote = FleetRemote(); a = remote.add('o/a', sources=[150, 151]); remote.add('o/b', sources=[150, 151])
    remote.conflict.add('o/a')
    result = gh_ops.fleet_apply(manifest(row(), row('o/b')), write=True, jobs=2, client=remote.client())
    assert not result['ok'] and result['succeeded'] == 1
    assert result['repositories'][0]['failed_stage'] == 'merge_source'
    assert a['prs'][150]['state'] == a['prs'][151]['state'] == 'open'
    assert not any(repo == 'o/a' and path.startswith('issues/') for repo, _, path, _ in writes(remote))


def test_file_commit_refusal_never_closes_sources():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151]); remote.refusal.add('o/a')
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    p = result['repositories'][0]
    assert not p['ok'] and p['failed_stage'] == 'commit_files' and p['closed'] == []
    assert r['prs'][150]['state'] == r['prs'][151]['state'] == 'open'


def test_source_moves_after_integration_is_not_closed():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151])
    def hook(repo, method, path, body):
        if path == 'pulls/149' and method == 'PATCH':
            r['refs']['source-150'] = r['refs']['main']
    remote.hook = hook
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    assert not result['ok'] and result['repositories'][0]['failed_stage'] == 'verify_sources'
    assert result['repositories'][0]['closed'] == []
    assert all(r['prs'][n]['state'] == 'open' for n in [150, 151])


def test_target_moves_during_comment_source_is_left_open():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151])
    def hook(repo, method, path, body):
        if path == 'issues/150/comments' and method == 'POST':
            r['refs']['work'] = r['refs']['main']
    remote.hook = hook
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    assert not result['ok'] and result['repositories'][0]['closed'] == []
    assert r['prs'][150]['state'] == 'open'


def test_comment_readback_failure_preserves_posted_receipt_leaves_source_open():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151]); remote.comment_read_error.add('o/a')
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    p = result['repositories'][0]
    assert not p['ok'] and p['failed_stage'] == 'comment_source'
    receipt = p['steps'][-1]
    assert receipt['posted'] and not receipt['verified'] and len(r['comments'][150]) == 1
    assert r['prs'][150]['state'] == 'open'
    remote.comment_read_error.clear(); remote.calls.clear()
    assert gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())['ok']
    assert len(r['comments'][150]) == 1


def test_partial_closure_reports_completed_and_remaining_and_retry_resumes():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151]); remote.close_error.add(('o/a', 151))
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    p = result['repositories'][0]
    assert p['closed'] == [150] and p['remaining_sources'] == [151] and not p['ok']
    assert r['prs'][150]['state'] == 'closed' and r['prs'][151]['state'] == 'open'
    remote.close_error.clear(); remote.calls.clear()
    assert gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())['ok']
    assert len(r['comments'][150]) == len(r['comments'][151]) == 1


def test_creation_lost_response_does_not_create_duplicate_on_rerun():
    remote = FleetRemote(); r = remote.add('o/a', target=False); remote.create_timeout.add('o/a')
    data = manifest({'repository': 'o/a', 'branch': 'batch/new', 'title': 'Batch', 'files': {'a': {'text': 'a'}}})
    first = gh_ops.fleet_apply(data, write=True, client=remote.client())
    assert not first['ok'] and r['created'] == 1
    assert gh_ops.fleet_apply(data, write=True, client=remote.client())['ok'] and r['created'] == 1


@pytest.mark.parametrize('bad', [
    manifest(row(), row(files={'a.txt': {'text': 'conflict'}})),
    manifest(row(), row(deletes=['a.txt'])),
    manifest(row(sources=[True])),
    manifest(row(target_pr=150)),
    manifest(row(files={'../bad': {'text': 'no'}})),
    manifest(row(unknown='typo')),
])
def test_invalid_manifest_fails_before_any_remote_call(bad):
    remote = FleetRemote()
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.fleet_apply(bad, write=True, client=remote.client())
    assert remote.calls == []


def test_all_local_files_loaded_before_any_remote_write(tmp_path):
    remote = FleetRemote(); remote.add('o/a')
    data = manifest({'repository': 'o/a', 'files': {'a': {'from': 'missing'}}})
    with pytest.raises(OSError):
        gh_ops.fleet_apply(data, root=str(tmp_path), write=True, client=remote.client())
    assert not remote.calls


def test_cli_resolves_files_relative_to_manifest_and_sets_exit_code(tmp_path, capsys):
    remote = FleetRemote(); remote.add('o/a')
    (tmp_path / 'input.txt').write_text('日本語\n')
    path = tmp_path / 'fleet.json'
    path.write_text(json.dumps(manifest({'repository': 'o/a', 'files': {'a': {'from': 'input.txt'}}})))
    assert gh_ops.main(['--json', 'fleet-apply', '--manifest', str(path), '--write'], client=remote.client()) == 0
    assert json.loads(capsys.readouterr().out)['succeeded'] == 1
    remote.conflict.add('o/a'); remote.add('o/b', sources=[150, 151])
    path.write_text(json.dumps(manifest(row('o/b'))))
    remote.conflict.add('o/b')
    assert gh_ops.main(['fleet-apply', '--manifest', str(path), '--write'], client=remote.client()) == 1


def test_permission_error_is_not_treated_as_a_missing_branch():
    remote = FleetRemote(); remote.add('o/a')
    def hook(repo, method, path, body):
        if path == 'git/ref/heads/work':
            raise gh_ops.GhOpsError('forbidden', http_status=403)
    remote.hook = hook
    result = gh_ops.fleet_apply(manifest({'repository': 'o/a', 'files': {'a': {'text': 'x'}}}),
                                write=True, client=remote.client())
    assert not result['ok'] and result['repositories'][0]['status'] == 'blocked'
    assert not writes(remote)


def test_source_ancestry_is_not_inferred_from_a_partial_compare_response():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150])
    original = remote.__call__
    def transport(method, url, body, headers):
        response = original(method, url, body, headers)
        if '/compare/' in url:
            response.data.pop('merge_base_commit')
        return response
    client = gh_ops.Client(token='t', transport=transport)
    result = gh_ops.fleet_apply(manifest(row(sources=[150], files={})), write=True, client=client)
    assert not result['ok'] and r['prs'][150]['state'] == 'open'
    assert not any(path.startswith('issues/') for _, _, path, _ in writes(remote))


def test_batch_plan_never_returns_full_source_bodies_or_local_paths():
    remote = FleetRemote(); r = remote.add('o/a', sources=[150, 151])
    r['prs'][150]['body'] = 'BODY_MUST_NOT_LEAK'
    result = gh_ops.fleet_apply(manifest(row()), client=remote.client())
    assert result['ok'] and 'BODY_MUST_NOT_LEAK' not in json.dumps(result)
    assert result['repositories'][0]['sources'][0]['number'] == 150


def test_default_render_shows_partial_result_and_remaining_sources():
    remote = FleetRemote(); remote.add('o/a', sources=[150, 151]); remote.close_error.add(('o/a', 151))
    result = gh_ops.fleet_apply(manifest(row()), write=True, client=remote.client())
    text = gh_ops._render('fleet-apply', result)
    assert '0/1 repositories' in text and 'closed=[150]' in text and 'remaining sources: [151]' in text


def test_multi_repo_resume_skips_completed_repo_and_integrated_sources():
    remote = FleetRemote()
    a = remote.add('o/a', sources=[150, 151])
    b = remote.add('o/b', sources=[150, 151])
    remote.refusal.add('o/a')
    data = manifest(row(), row('o/b'))
    first = gh_ops.fleet_apply(data, write=True, jobs=2, client=remote.client())
    assert not first['ok'] and first['succeeded'] == 1
    assert first['repositories'][0]['failed_stage'] == 'commit_files'
    assert first['repositories'][0]['remaining_sources'] == [150, 151]
    assert all(a['prs'][n]['state'] == 'open' for n in [150, 151])
    assert all(b['prs'][n]['state'] == 'closed' for n in [150, 151])
    remote.refusal.clear()
    remote.calls.clear()
    resumed = gh_ops.fleet_apply(data, write=True, jobs=2, client=remote.client())
    assert resumed['ok'] and resumed['succeeded'] == 2
    assert a['created'] == b['created'] == 0
    assert not any(repo == 'o/b' for repo, *_ in writes(remote))
    assert not any(path == 'merges' for _, _, path, _ in writes(remote))
    assert all(r['prs'][149]['state'] == 'open' for r in [a, b])
    assert all(len(r['comments'][n]) == 1 for r in [a, b] for n in [150, 151])


def test_fleet_append_partial_overlap_then_resume_has_one_complete_block():
    remote = FleetRemote()
    r = remote.add('o/a')
    r['prs'][149]['body'] = 'foobar'
    data = manifest(row(sources=[], append_body='foo'))
    first = gh_ops.fleet_apply(data, write=True, client=remote.client())
    assert first['ok'] and r['prs'][149]['body'] == 'foobar\n\nfoo'
    remote.calls.clear()
    assert gh_ops.fleet_apply(data, write=True, client=remote.client())['ok']
    assert not writes(remote) and r['created'] == 0


# ═══ · gh ops fleet issues ═══════════════════════════════════════════════════════════════════════════════
# fleet-issues: one Issue per remaining fact of a fleet report, matched by key, numbers kept, dry-run first.

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gh_ops_push import gh_ops as gh_ops_gh_ops_fleet_issues, REPO  # noqa: E402

REPORT = {
    "schema": "vendor-fleet-report/1", "generated_at": "2026-11-09T04:00:00+00:00", "parent_revision": "5d6f424f" + "0" * 32,
    "remaining": [
        {"severity": "updated", "key": "updated|vendor|xprobe:xprobe.py|blob →9d9071f", "no": "FLEET-1", "what": "📥 xprobe:xprobe.py",
         "detail": "blob 7595fe5→9d9071f → 2× BrowserTestKit (tests/vendor/xprobe/xprobe.py), Err-o-RoadRunner (vendor/xprobe.py)",
         "repositories": ["BrowserTestKit", "Err-o-RoadRunner"], "rows": 2},
        {"severity": "failure", "key": "failure|vendor|PySaiyanWeBoo:|source path removed", "no": "FLEET-2", "what": "📥 PySaiyanWeBoo:",
         "detail": "source path removed at dabd14b → 3× Ironmate (a.py ← x.py, b.py ← y.py), github.io (c.css ← z.css)",
         "repositories": ["Ironmate", "github.io"], "rows": 3},
        {"severity": "insufficient", "key": "target:BrowserTestKit", "no": "FLEET-3", "what": "BrowserTestKit",
         "detail": "registry ≠ vendor.lock.json: missing 4, unexpected 0", "repositories": ["BrowserTestKit"]},
    ],
}


class Issues:
    """A transport serving /issues (list, create, patch, comments) and /labels for one repository."""

    def __init__(self, issues=(), labels=("bug", "enhancement")):
        self.issues = {issue["number"]: dict(issue) for issue in issues}
        self.labels = list(labels)
        self.calls = []
        self.comments = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        self.calls.append((method, path))
        ok = lambda data, status=200: gh_ops_gh_ops_fleet_issues.Response(status, data, {})
        if path == f"/repos/{REPO}/labels":
            return ok([{"name": name} for name in self.labels])
        if path == f"/repos/{REPO}/issues" and method == "GET":
            return ok([dict(i, labels=[{"name": n} for n in i.get("label_names", [])]) for i in self.issues.values()])
        if path == f"/repos/{REPO}/issues" and method == "POST":
            number = max([100, *self.issues]) + 1
            self.issues[number] = {"number": number, "state": "open", "title": body["title"], "body": body["body"],
                                   "label_names": body.get("labels", []), "html_url": f"https://github.com/{REPO}/issues/{number}"}
            return ok(self.issues[number], 201)
        if path.startswith(f"/repos/{REPO}/issues/") and path.endswith("/comments") and method == "POST":
            self.comments.append((int(path.split("/")[-2]), body["body"]))
            return ok({"id": len(self.comments)}, 201)
        if path.startswith(f"/repos/{REPO}/issues/") and method == "PATCH":
            issue = self.issues[int(path.rsplit("/", 1)[1])]
            for field in ("title", "body", "state"):
                if field in body:
                    issue[field] = body[field]
            if "labels" in body:
                issue["label_names"] = body["labels"]
            return ok(issue)
        raise AssertionError((method, path))

    def client(self):
        return gh_ops_gh_ops_fleet_issues.Client(token="t0ken", transport=self)


def report_file(tmp_path, report=REPORT):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return str(path)


def issue(number, key, no, *, state="open", title=None):
    return {"number": number, "state": state, "title": title or f"{no} 📥 old title", "body": f"{gh_ops_gh_ops_fleet_issues.FLEET_MARKER}{key} -->\n\nold",
            "html_url": f"https://github.com/{REPO}/issues/{number}", "label_names": []}


def test_plan_creates_nothing_and_shows_every_action(tmp_path):
    remote = Issues()
    plan = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    assert plan["ok"] and plan["dry_run"] and plan["actions"] == {"create": 3, "update": 0, "reopen": 0, "unchanged": 0, "close": 0}
    assert all(call[0] == "GET" for call in remote.calls) and plan["writes"] == 0
    rows = {row["key"]: row for row in plan["rows"]}
    assert rows[REPORT["remaining"][0]["key"]]["title"] == "FLEET-1 📥 xprobe:xprobe.py · blob 7595fe5→9d9071f (2×)"
    assert rows[REPORT["remaining"][0]["key"]]["labels"] == ["enhancement"]
    assert rows[REPORT["remaining"][1]["key"]]["labels"] == ["bug"]
    text = gh_ops_gh_ops_fleet_issues._render("fleet-issues", plan)
    assert text.startswith("OK fleet-issues plan") and "🆕 #? FLEET-2 📥 PySaiyanWeBoo: · source path removed at dabd14b (3×) [bug]" in text
    long = dict(REPORT["remaining"][1], detail="source path removed at dabd14b; the pinned commit still has it (re-point) → 1× Ironmate (a.py)")
    assert gh_ops_gh_ops_fleet_issues.fleet_issue_text(long, REPORT)[0] == "FLEET-2 📥 PySaiyanWeBoo: · source path removed at dabd14b (3×)"
    assert "re-point" in gh_ops_gh_ops_fleet_issues.fleet_issue_text(long, REPORT)[1]


def test_write_creates_updates_reopens_keeps_numbers_and_verifies(tmp_path):
    existing = [issue(7, REPORT["remaining"][0]["key"], "FLEET-9"),               # same fact, numbered 9 by an earlier run
                issue(8, REPORT["remaining"][1]["key"], "FLEET-4", state="closed")]  # came back after being closed
    remote = Issues(existing, labels=("bug", "enhancement", "fleet"))
    done = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, write=True, client=remote.client())
    assert done["ok"] and not done["dry_run"]
    assert done["actions"] == {"create": 1, "update": 1, "reopen": 1, "unchanged": 0, "close": 0}
    by_key = {row["key"]: row for row in done["rows"]}
    first = by_key[REPORT["remaining"][0]["key"]]
    assert (first["no"], first["number"], first["action"]) == ("FLEET-9", 7, "update")  # the Issue's number wins
    assert remote.issues[7]["title"].startswith("FLEET-9 📥 xprobe:xprobe.py")
    second = by_key[REPORT["remaining"][1]["key"]]
    assert (second["no"], second["action"], remote.issues[8]["state"]) == ("FLEET-4", "reopen", "open")
    third = by_key["target:BrowserTestKit"]
    assert third["action"] == "create" and third["no"] == "FLEET-3" and third["number"] == 101
    assert remote.issues[101]["label_names"] == ["bug", "fleet"]
    assert all(row["verified"] for row in done["rows"])
    body = remote.issues[7]["body"]
    assert body.startswith(gh_ops_gh_ops_fleet_issues.FLEET_MARKER + REPORT["remaining"][0]["key"] + " -->")
    assert "- BrowserTestKit (tests/vendor/xprobe/xprobe.py)" in body and "- Err-o-RoadRunner (vendor/xprobe.py)" in body
    assert "- Ironmate (a.py ← x.py, b.py ← y.py)" in remote.issues[8]["body"]  # commas inside parentheses stay together
    again = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, write=True, client=remote.client())
    assert again["actions"]["unchanged"] == 3 and again["writes"] == 0


def test_new_facts_avoid_numbers_any_issue_holds(tmp_path):
    remote = Issues([issue(5, "other|fact", "FLEET-3")])  # FLEET-3 is taken by a fact not in this report
    plan = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    numbers = {row["key"]: row["no"] for row in plan["rows"]}
    assert numbers["target:BrowserTestKit"] == "FLEET-4"
    assert len(set(numbers.values())) == 3


def test_close_resolved_only_with_the_flag_and_only_open_fleet_issues(tmp_path):
    remote = Issues([issue(5, "gone|fact", "FLEET-3"), issue(6, "gone|closed", "FLEET-8", state="closed"),
                     {"number": 9, "state": "open", "title": "unrelated issue", "body": "no marker", "html_url": "", "label_names": []}])
    plain = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    assert plain["actions"]["close"] == 0
    plan = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, close_resolved=True, client=remote.client())
    assert [row["number"] for row in plan["rows"] if row["action"] == "close"] == [5]
    done = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, close_resolved=True, write=True, client=remote.client())
    assert done["ok"] and remote.issues[5]["state"] == "closed" and remote.issues[9]["state"] == "open"
    assert remote.comments and remote.comments[0][0] == 5 and "閉じます" in remote.comments[0][1]


def test_severity_filter_and_bad_report(tmp_path):
    remote = Issues()
    plan = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path), REPO, severities=("failure",), client=remote.client())
    assert [row["severity"] for row in plan["rows"]] == ["failure"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(gh_ops_gh_ops_fleet_issues.GhOpsError):
        gh_ops_gh_ops_fleet_issues.fleet_issues(str(bad), REPO, client=remote.client())


def test_cli_plan_and_real_report_shape(tmp_path, capsys):
    remote = Issues()
    assert gh_ops_gh_ops_fleet_issues.main(["fleet-issues", report_file(tmp_path), "--repo", REPO], client=remote.client()) == 0
    assert "fleet-issues plan" in capsys.readouterr().out
    # The real producer's remaining items file without any adaptation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import vendor_fleet_plan as fleet
    result = {"graph": {"cycles": [["a:x", "b:y", "a:x"]], "findings": [{"repository": "r", "kind": "two-sources", "subject": "r:v.py",
                                                                        "detail": "v.py is copied from a:x and b:y"}]},
              "rows": [{"id": "vendor:r:v.py<-a:x", "kind": "vendor", "status": "updated", "reason": "blob 1→2", "source": "a:x",
                        "target": "r:v.py", "repository": "r"}],
              "targets": {"repositories": [{"repository": "s", "category": "insufficient", "reasons": ["lock outside every hub"]}]},
              "plan": {"steps": []}}
    items = fleet.remaining(result)
    real = {"schema": "vendor-fleet-report/1", "generated_at": "2026-11-09T05:00:00+00:00", "remaining": items}
    plan = gh_ops_gh_ops_fleet_issues.fleet_issues(report_file(tmp_path, real), REPO, client=remote.client())
    assert plan["actions"]["create"] == 4
    titles = [row["title"] for row in plan["rows"]]
    assert any(t.startswith("FLEET-1 execution cycle") for t in titles)
    assert any("📥 a:x · blob 1→2" in t for t in titles)


# ═══ · gh ops pr edit body ═══════════════════════════════════════════════════════════════════════════════
# Existing PR consolidation without ad-hoc gh api scripts.

PATH = '/repos/octo/demo/pulls/11'
BLOCK = '## Consolidated scope\n- #150 を取り込み 🐍'
BEFORE = pr_payload(title='Old', body='Existing\r\n日本語')
AFTER = pr_payload(title='Updated', body=BEFORE['body'] + '\n\n' + BLOCK)


def routes(gets, patch=None):
    return {('GET', PATH): [reply(x) for x in gets], ('PATCH', PATH): patch or reply({})}


def test_title_and_append_are_one_patch_and_verified():
    client, stub = client_for(routes([BEFORE, BEFORE, AFTER]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, title='Updated', append_body=BLOCK, write=True, client=client)
    assert result['ok'] and result['status'] == 'verified'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET']
    assert stub.calls[2]['body'] == {'title': 'Updated', 'body': AFTER['body']}


@pytest.mark.parametrize('old', [None, '', 'Existing'])
def test_append_dry_run_preserves_old_body(old):
    client, stub = client_for(routes([pr_payload(body=old)]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body=BLOCK, client=client)
    assert result['status'] == 'planned' and stub.methods == ['GET']
    assert result['fields']['body']['to'] == (old + '\n\n' if old else '') + BLOCK


def test_retry_skips_duplicate_append_even_after_later_notes():
    client, stub = client_for(routes([{**AFTER, 'body': AFTER['body'] + '\n\nLater note'}]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, title='Updated', append_body=BLOCK, write=True, client=client)
    assert result['status'] == 'already_satisfied' and not result['changed']
    assert stub.methods == ['GET']


@pytest.mark.parametrize('change', [{'body': 'concurrent edit'}, {'head': {'sha': 'new'}}, {'title': 'concurrent title'}])
def test_append_refuses_observed_concurrent_edit(change):
    client, stub = client_for(routes([BEFORE, {**BEFORE, **change}]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body=BLOCK, write=True, client=client)
    assert not result['ok'] and result['status'] == 'stale'
    assert stub.methods == ['GET', 'GET']


@pytest.mark.parametrize('http,status', [(403, 'mutation_failed'), (502, 'mutation_uncertain')])
def test_patch_errors_keep_distinct_receipts(http, status):
    client, stub = client_for(routes([BEFORE], reply({}, status=http)))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, state='closed', write=True, client=client)
    assert not result['ok'] and result['status'] == status
    assert stub.methods == ['GET', 'PATCH']


def test_readback_failure_preserves_successful_mutation():
    client, stub = client_for({('GET', PATH): [reply(BEFORE), reply({}, status=503)],
                               ('PATCH', PATH): reply({})})
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, state='closed', write=True, client=client)
    assert result['status'] == 'verification_failed' and result['changed']
    assert result['mutation_status'] == 'succeeded' and not result['ok']
    assert stub.methods == ['GET', 'PATCH', 'GET']


@pytest.mark.parametrize('kwargs', [{'body': '', 'append_body': BLOCK}, {'append_body': ' \n'}, {'title': ''}])
def test_invalid_edit_before_request(kwargs):
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, client=client, **kwargs)
    assert not stub.calls


def test_cli_file_preserves_unicode_and_real_crlf(tmp_path, capsys):
    path = tmp_path / 'body.md'
    body = '新しい本文\r\n\r\nlast\n'
    path.write_bytes(body.encode())
    client, stub = client_for(routes([BEFORE, {**BEFORE, 'body': body, 'title': 'Updated'}]))
    assert gh_ops.main(['--json', 'pr-edit', REPO_gh_ops_pr_edit_body, '11', '--title', 'Updated',
                        '--body-file', str(path), '--write'], client=client) == 0
    assert json.loads(capsys.readouterr().out)['verified']
    assert stub.calls[1]['body'] == {'title': 'Updated', 'body': body}


def test_cli_append_file(tmp_path, capsys):
    path = tmp_path / 'scope.md'
    path.write_text(BLOCK, encoding='utf-8')
    client, _ = client_for(routes([BEFORE]))
    assert gh_ops.main(['--json', 'pr-edit', REPO_gh_ops_pr_edit_body, '11', '--append-body-file', str(path)], client=client) == 0
    assert json.loads(capsys.readouterr().out)['fields']['body']['to'] == AFTER['body']


@pytest.mark.parametrize('old,block,present', [
    ('foobar', 'foo', False), ('barfoo', 'foo', False),
    ('foo\nbar', 'foo', False), ('bar\nfoo', 'foo', False),
    ('foo', 'foo', True), ('foo\n', 'foo', True), ('\nfoo', 'foo', True),
    ('foobar\n\nfoo\n\nLater', 'foo', True),
    ('intro\n\nfirst\n\nsecond extended', 'first\n\nsecond', False),
    ('intro\n\nfirst\n\nsecond\n\nLater', 'first\n\nsecond', True),
    ('foo\r\nbar', 'foo', False), ('bar\r\nfoo', 'foo', False),
    ('bar\rfoo', 'foo', False), ('foo\rbar', 'foo', False),
    ('\r\nfoo\r\n', 'foo', True),
])
def test_append_requires_a_complete_block(old, block, present):
    current = pr_payload(body=old)
    expected = old if present else old.rstrip() + '\n\n' + block
    client, stub = client_for(routes([current, current, pr_payload(body=expected)]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body=block, write=True, client=client)
    assert result['ok']
    assert result['fields']['body']['to'] == expected
    assert result['status'] == ('already_satisfied' if present else 'verified')
    assert stub.methods == (['GET'] if present else ['GET', 'GET', 'PATCH', 'GET'])


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_mult_paragraph_block_preserves_internal_newlines_and_retries(newline):
    block = f'first{newline}{newline}日本語 🐍{newline}last'
    old = f'prefix-{block}-suffix'  # Partial overlap must not suppress an append.
    appended = old + '\n\n' + block
    after = pr_payload(body=appended)
    later = pr_payload(body=appended + newline * 2 + 'Later note')
    client, stub = client_for(routes([pr_payload(body=old), pr_payload(body=old), after, later]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body=newline + block + newline, write=True, client=client)
    assert result['verified'] and stub.calls[2]['body'] == {'body': appended}
    retry = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body=block, write=True, client=client)
    assert retry['status'] == 'already_satisfied'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET', 'GET']


def test_concurrent_append_is_stale_then_retry_preserves_the_other_edit():
    original = pr_payload(body='foobar')
    concurrent = pr_payload(body='foobar\n\nSomeone else added this')
    after = pr_payload(body=concurrent['body'] + '\n\nfoo')
    client, stub = client_for(routes([original, concurrent, concurrent, concurrent, after, after]))
    first = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body='foo', write=True, client=client)
    assert first['status'] == 'stale' and stub.methods == ['GET', 'GET']
    assert gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body='foo', write=True, client=client)['verified']
    assert gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body='foo', write=True, client=client)['status'] == 'already_satisfied'
    assert [c['body'] for c in stub.calls if c['method'] == 'PATCH'] == [{'body': after['body']}]


def test_concurrent_change_at_readback_does_not_report_success():
    current = pr_payload(body='foobar')
    changed = pr_payload(body='foobar\n\nfoo\n\nConcurrent note')
    client, stub = client_for(routes([current, current, changed]))
    result = gh_ops.pr_edit(REPO_gh_ops_pr_edit_body, 11, append_body='foo', write=True, client=client)
    assert not result['ok'] and result['status'] == 'verification_mismatch'
    assert result['mutation_status'] == 'succeeded'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET']


# ═══ · gh ops records import ═════════════════════════════════════════════════════════════════════════════
# records-import: merge a dashboard export into site/records.jsonl, locally or as one commit (+PR).

from test_gh_ops_push import Remote, gh_ops as gh_ops_gh_ops_fleet_issues, REPO  # noqa: E402

EXPORT = {"schema": "web-ui-drafts/1", "exported": "2026-11-08T04:00:00Z",
          "drafts": {"Demo#4:state": "done", "Demo#4:notes": "", "Demo#5:state": "doing", "Demo#5:notes": "rename only"}}
CURRENT = '{"id": "Demo#4", "state": "doing", "notes": "merged first", "next": "watch CI"}\n{"id": "Demo#2", "state": "waiting"}\n'


def test_merge_keeps_order_and_unrelated_fields_and_removes_empty_fields():
    merged = gh_ops_gh_ops_fleet_issues.merge_records(CURRENT, gh_ops_gh_ops_fleet_issues.drafts_from_export(EXPORT))
    assert (merged["added"], merged["updated"], merged["removed"], merged["unchanged"]) == (["Demo#5"], ["Demo#4"], [], [])
    rows = [json.loads(line) for line in merged["text"].splitlines()]
    assert rows == [{"id": "Demo#4", "next": "watch CI", "state": "done"}, {"id": "Demo#2", "state": "waiting"},
                    {"id": "Demo#5", "notes": "rename only", "state": "doing"}]
    again = gh_ops_gh_ops_fleet_issues.merge_records(merged["text"], gh_ops_gh_ops_fleet_issues.drafts_from_export(EXPORT))
    assert again["unchanged"] == ["Demo#4", "Demo#5"] and again["text"] == merged["text"]
    gone = gh_ops_gh_ops_fleet_issues.merge_records(merged["text"], {"Demo#2": {"state": ""}})
    assert gone["removed"] == ["Demo#2"] and "Demo#2" not in gone["text"]


def test_export_validation():
    assert gh_ops_gh_ops_fleet_issues.drafts_from_export({"Demo#i9:notes": "bare mapping"}) == {"Demo#i9": {"notes": "bare mapping"}}
    for bad in ({"drafts": {"x:foo": "1"}}, {"drafts": {"Demo#1:state": "nope"}}, {"drafts": {"Demo#1:notes": 3}}, [], {"drafts": {"no-number:state": ""}}):
        with pytest.raises(gh_ops_gh_ops_fleet_issues.GhOpsError):
            gh_ops_gh_ops_fleet_issues.drafts_from_export(bad)
    with pytest.raises(gh_ops_gh_ops_fleet_issues.GhOpsError):
        gh_ops_gh_ops_fleet_issues.records_from_jsonl('{"state": "done"}\n')


def test_local_import_plans_then_writes(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps(EXPORT), encoding="utf-8")
    records = tmp_path / "site" / "records.jsonl"
    plan = gh_ops_gh_ops_fleet_issues.records_import(str(export), records_path=str(records))
    assert plan["ok"] and plan["dry_run"] and plan["added"] == ["Demo#4", "Demo#5"] and not records.exists()
    done = gh_ops_gh_ops_fleet_issues.records_import(str(export), records_path=str(records), write=True)
    assert done["written"] and json.loads(records.read_text().splitlines()[1]) == {"id": "Demo#5", "notes": "rename only", "state": "doing"}
    text = gh_ops_gh_ops_fleet_issues._render("records-import", done)
    assert text.startswith("OK records-import") and "added     Demo#4, Demo#5" in text


class RecordsRemote(Remote):
    """The commit-files stub plus repository contents, ref creation and pulls."""

    def __init__(self, files, *, branch_exists=False):
        super().__init__(files)
        self.branch_exists = branch_exists
        self.pulls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        ok = lambda data, status=200: gh_ops_gh_ops_fleet_issues.Response(status, data, {})
        if "/contents/" in path and method == "GET":
            self.calls.append((method, path))
            name = urllib.parse.unquote(path.split("/contents/", 1)[1])
            tree = self.trees[self.commits[self.head]]
            if name not in tree:
                return ok({"message": "Not Found"}, 404)
            data = self.blobs[name]
            return ok({"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if "/git/ref/heads/" in path and method == "GET" and not self.branch_exists and path.endswith("/audit%2Frecords"):
            self.calls.append((method, path))
            return ok({"message": "Not Found"}, 404)
        if path.endswith("/git/refs") and method == "POST":
            self.calls.append((method, path))
            assert body == {"ref": "refs/heads/audit/records", "sha": self.head}
            self.branch_exists = True
            return ok({"ref": body["ref"]}, 201)
        if path.endswith("/pulls") and method == "GET":
            self.calls.append((method, path))
            return ok(self.pulls)
        if path.endswith("/pulls") and method == "POST":
            self.calls.append((method, path))
            self.pulls.append({"number": 7, "head": {"ref": body["head"]}})
            return ok({"number": 7}, 201)
        return super().__call__(method, url, body, headers)


def test_remote_import_creates_branch_commits_once_and_opens_a_pr(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps(EXPORT), encoding="utf-8")
    remote = RecordsRemote({"site/records.jsonl": CURRENT.encode()})
    remote.blobs = {"site/records.jsonl": CURRENT.encode()}
    plan = gh_ops_gh_ops_fleet_issues.records_import(str(export), repo=REPO, branch="audit/records", open_pr=True, client=remote.client())
    assert plan["ok"] and plan["dry_run"] and plan["source_ref"] == "main" and plan["plan"] == {"create_branch": True, "open_pr": True}
    assert {m for m, _ in remote.calls} == {"GET"}
    done = gh_ops_gh_ops_fleet_issues.records_import(str(export), repo=REPO, branch="audit/records", open_pr=True, write=True, client=remote.client())
    assert done["ok"] and done["created_branch"] and done["commit"]["ok"] and done["pr"] == 7
    committed = remote.trees[remote.commits[remote.head]]["site/records.jsonl"]
    expected = gh_ops_gh_ops_fleet_issues.merge_records(CURRENT, gh_ops_gh_ops_fleet_issues.drafts_from_export(EXPORT))["text"].encode()
    assert committed == gh_ops_gh_ops_fleet_issues._git_blob_sha(expected)
    assert sum(1 for m, p in remote.calls if m == "POST" and p.endswith("/pulls")) == 1
    text = gh_ops_gh_ops_fleet_issues._render("records-import", done)
    assert "-> octo/demo@audit/records (applied)" in text and "pr        #7" in text


def test_remote_import_with_nothing_to_change_writes_nothing(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps({"drafts": {"Demo#4:state": "doing"}}), encoding="utf-8")
    remote = RecordsRemote({"site/records.jsonl": CURRENT.encode()}, branch_exists=True)
    remote.blobs = {"site/records.jsonl": CURRENT.encode()}
    done = gh_ops_gh_ops_fleet_issues.records_import(str(export), repo=REPO, branch="audit/records", write=True, client=remote.client())
    assert done["ok"] and done["reason"] == "records already up to date" and done["source_ref"] == "audit/records"
    assert {m for m, _ in remote.calls} == {"GET"}
    with pytest.raises(gh_ops_gh_ops_fleet_issues.GhOpsError):
        gh_ops_gh_ops_fleet_issues.records_import(str(export), repo=REPO, client=remote.client())


# ═══ · gh ops variable comment ═══════════════════════════════════════════════════════════════════════════
# variable-delete and issue-comment-upsert: writes live in gh_ops.py, not GHI.

VAR = "/repos/octo/demo/actions/variables/FOO"
VARIABLES = "/repos/octo/demo/actions/variables"
PR = "/repos/octo/demo/pulls/11"
COMMENTS = "/repos/octo/demo/issues/11/comments"
BOT = "github-actions[bot]"
MARK = "<!-- digest -->"


def test_variable_delete_dry_run_never_deletes():
    client, stub = client_for({("GET", VAR): reply({"name": "FOO", "value": "secret-ish"})})
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", client=client)
    assert result["ok"] and result["dry_run"] and result["status"] == "would_delete"
    assert stub.methods == ["GET"] and "value" not in str(result)


def test_variable_delete_verified_only_with_complete_scoped_listing():
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({"message": "Not Found"}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): reply({"total_count": 0, "variables": []}),
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] and result["verified"] and result["status"] == "verified"
    assert stub.methods == ["GET", "DELETE", "GET", "GET"]


def test_variable_delete_403_after_delete_is_uncertain_not_verified():
    client, _ = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({"message": "Forbidden"}, status=403)],
        ("DELETE", VAR): reply(None, status=204),
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] is False and result["status"] == "verification_uncertain" and result["http_status"] == 403


def test_variable_delete_403_before_is_inaccessible_not_already_absent():
    client, stub = client_for({("GET", VAR): reply({"message": "Forbidden"}, status=403)})
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] is False and result["status"] == "inaccessible"
    assert stub.methods == ["GET"]


def test_variable_delete_404_before_requires_complete_listing():
    client, stub = client_for({
        ("GET", VAR): reply({"message": "Not Found"}, status=404),
        ("GET", VARIABLES): reply({"total_count": 0, "variables": []}),
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] and result["status"] == "already_absent" and stub.methods == ["GET", "GET"]


def test_variable_delete_rejects_bad_names_before_http():
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "../x", client=client)
    assert stub.calls == []


def test_upsert_ignores_other_authors_and_patches_own_comment():
    comments = [
        {"id": 1, "user": {"login": "someone"}, "body": MARK + " theirs"},
        {"id": 2, "user": {"login": BOT}, "body": MARK + " old"},
    ]
    client, stub = client_for({
        ("GET", PR): reply(pr_payload()),
        ("GET", COMMENTS): reply(comments),
        ("PATCH", "/repos/octo/demo/issues/comments/2"): reply({"id": 2}),
        ("GET", "/repos/octo/demo/issues/comments/2"): reply({"id": 2, "body": MARK + " new"}),
    })
    result = gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=MARK + " new", marker=MARK, author=BOT,
                                         write=True, client=client)
    assert result["ok"] and result["action"] == "update" and result["comment_id"] == 2


def test_upsert_creates_when_only_other_authors_have_the_marker():
    from test_gh_ops import pr_payload
    client, stub = client_for({
        ("GET", COMMENTS): reply([{"id": 1, "user": {"login": "someone"}, "body": MARK}]),
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload()),
    })
    result = gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=MARK + " x", marker=MARK, author=BOT, client=client)
    assert result["ok"] and result["dry_run"] and result["action"] == "create"


def test_upsert_requires_marker_prefix():
    client, _ = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body="x", marker=MARK, author=BOT, client=client)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
@pytest.mark.parametrize("after_write", [False, True])
def test_inaccessible_variable_list_never_proves_absence(status, after_write):
    missing = reply({"message": "Not Found"}, status=404)
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), missing] if after_write else missing,
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): reply({"message": "unavailable"}, status=status),
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] is False and not result.get("verified", False)
    assert result["status"] == ("verification_uncertain" if after_write else "inaccessible")
    assert result["deleted"] is after_write
    assert ("DELETE" in stub.methods) is after_write


@pytest.mark.parametrize("payload", [
    {}, {"variables": []}, {"total_count": 1, "variables": []},
    {"total_count": True, "variables": []}, {"total_count": -1, "variables": []},
    {"total_count": 0, "variables": {}}, {"total_count": 1, "variables": [{}]},
    {"total_count": 1, "variables": [{"name": 42}]},
    {"total_count": 2, "variables": [{"name": "BAR"}, {"name": "bar"}]},
])
def test_malformed_or_incomplete_variable_list_is_uncertain(payload):
    client, _ = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): reply(payload),
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["ok"] is False and result["status"] == "verification_uncertain"
    assert result["deleted"] is True and result["mutation_status"] == "succeeded"


@pytest.mark.parametrize("second_name, verified", [("BAR", True), ("foo", False)])
def test_variable_absence_checks_every_page_and_case_insensitive_names(second_name, verified):
    link = '<https://api.github.com/repos/octo/demo/actions/variables?per_page=100&page=2>; rel="next"'
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): [reply({"total_count": 2, "variables": [{"name": "BAZ", "value": "private-value"}]}, link=link),
                             reply({"total_count": 2, "variables": [{"name": second_name}]})],
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert result["verified"] is verified and result["ok"] is verified
    assert result["status"] == ("verified" if verified else "verification_failed")
    assert "private-value" not in str(result)
    assert stub.calls[-1]["query"]["page"] == ["2"]


def test_variable_list_changed_total_or_cycle_is_uncertain():
    link = '<https://api.github.com/repos/octo/demo/actions/variables?per_page=100&page=2>; rel="next"'
    for second in (reply({"total_count": 3, "variables": [{"name": "BAR"}]}),
                   reply({"total_count": 2, "variables": [{"name": "BAR"}]}, link=link)):
        client, _ = client_for({
            ("GET", VAR): [reply({"name": "FOO"}), reply({}, status=404)],
            ("DELETE", VAR): reply(None, status=204),
            ("GET", VARIABLES): [reply({"total_count": 2, "variables": [{"name": "BAZ"}]}, link=link), second],
        })
        result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
        assert not result["ok"] and result["status"] == "verification_uncertain"


@pytest.mark.parametrize("status, uncertain", [(403, False), (500, True), (None, True)])
def test_variable_mutation_failure_preserves_uncertainty(status, uncertain):
    def failure(*unused):
        raise gh_ops.GhOpsError("failure", http_status=status)
    client, stub = client_for({("GET", VAR): reply({"name": "FOO"}), ("DELETE", VAR): failure})
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert not result["ok"] and not result["verified"]
    assert result["status"] == ("mutation_uncertain" if uncertain else "mutation_failed")
    assert result["deleted"] is (None if uncertain else False)
    assert stub.methods == ["GET", "DELETE"]


@pytest.mark.parametrize("status", [403, 404, 500])
def test_upsert_preserves_successful_patch_when_readback_fails(status):
    path = "/repos/octo/demo/issues/comments/2"
    client, stub = client_for({
        ("GET", PR): reply(pr_payload()),
        ("GET", COMMENTS): reply([{"id": 2, "user": {"login": BOT}, "body": MARK + " old"}]),
        ("PATCH", path): reply({"id": 2}),
        ("GET", path): reply({"message": "unavailable"}, status=status),
    })
    result = gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=MARK + " new", marker=MARK, author=BOT,
                                         write=True, client=client)
    assert not result["ok"] and not result["verified"]
    assert result["updated"] is True and result["mutation_status"] == "succeeded"
    assert result["status"] == "verification_uncertain" and result["comment_id"] == 2
    assert stub.methods.count("PATCH") == 1


def test_upsert_rejects_issue_target_before_mutation():
    client, stub = client_for({("GET", PR): reply({}, status=404)})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=MARK + " new", marker=MARK, author=BOT,
                                    write=True, client=client)
    assert stub.methods == ["GET"]


def test_variable_list_page_limit_is_uncertain():
    def page(parts, body):
        from urllib.parse import parse_qs
        number = int(parse_qs(parts.query).get("page", ["1"])[0])
        link = f'<https://api.github.com/repos/octo/demo/actions/variables?per_page=100&page={number + 1}>; rel="next"'
        return reply({"total_count": 51, "variables": [{"name": f"OTHER_{number}"}]}, link=link)
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): page,
    })
    result = gh_ops.variable_delete(REPO_gh_ops_pr_edit_body, "FOO", write=True, client=client)
    assert not result["ok"] and result["status"] == "verification_uncertain"
    assert sum(call["path"] == VARIABLES for call in stub.calls) == 50


@pytest.mark.parametrize("status, uncertain", [(403, False), (500, True), (None, True)])
def test_upsert_preserves_patch_failure_or_uncertainty(status, uncertain):
    def failure(*unused):
        raise gh_ops.GhOpsError("failure", http_status=status)
    path = "/repos/octo/demo/issues/comments/2"
    client, stub = client_for({
        ("GET", PR): reply(pr_payload()),
        ("GET", COMMENTS): reply([{"id": 2, "user": {"login": BOT}, "body": MARK + " old"}]),
        ("PATCH", path): failure,
    })
    result = gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=MARK + " new", marker=MARK, author=BOT,
                                         write=True, client=client)
    assert not result["ok"] and not result["verified"]
    assert result["updated"] is (None if uncertain else False)
    assert result["status"] == ("mutation_uncertain" if uncertain else "mutation_failed")
    assert stub.methods == ["GET", "GET", "PATCH"]


def test_upsert_dry_run_and_unchanged_are_read_only():
    for body, action in ((MARK + " old", "unchanged"), (MARK + " new", "update")):
        client, stub = client_for({
            ("GET", PR): reply(pr_payload()),
            ("GET", COMMENTS): reply([{"id": 2, "user": {"login": BOT}, "body": MARK + " old"}]),
        })
        result = gh_ops.issue_comment_upsert(REPO_gh_ops_pr_edit_body, 11, body=body, marker=MARK, author=BOT, client=client)
        assert result["ok"] and result["dry_run"] and result["action"] == action
        assert stub.methods == ["GET", "GET"]


# ═══ · gh ops write surface ══════════════════════════════════════════════════════════════════════════════
# Parent GitHub write files stay on an explicit reviewed list.

ALLOW = (
    "gh_ops.py",
    "gh_workflow.py",
    "vendor_sync.py",
    "vendor/gh_identity.py",
    ".github/workflows/sync-readme.yml",
    ".github/workflows/update-readme-stats.yml",
)
SKIP_PREFIXES = ("tests/", "docs/", ".git/")
# Pattern hits that are not GitHub writes: the vendored pysaiyanweboo.py carries a browser-side curl builder
# whose JS sets method="POST" for arbitrary HTTP requests the page displays; it never calls the GitHub API.
NOT_GITHUB = ("vendor/pysaiyanweboo.py",)
SUFFIXES = {".py", ".yml", ".yaml", ".mjs", ".cjs", ".js"}
WRITE_PATTERNS = (
    re.compile(r'_github_api\(\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'\.request\(\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'method\s*=\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'\bgit commit\b'),
    re.compile(r'\bgit push\b'),
    re.compile(r'--method'),
)


def _tracked_files():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _write_files():
    found = []
    for rel in _tracked_files():
        path = ROOT / rel
        if path.suffix not in SUFFIXES or rel.startswith(SKIP_PREFIXES) or rel in NOT_GITHUB or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in WRITE_PATTERNS):
            found.append(rel)
    return sorted(found)


def test_reviewed_write_files_still_contain_a_write():
    found = set(_write_files())
    missing = [path for path in ALLOW if path not in found]
    assert not missing, "reviewed write file no longer matches: " + ", ".join(missing)


def test_no_unlisted_parent_write_surface():
    extra = [path for path in _write_files() if path not in ALLOW]
    assert not extra, "unlisted GitHub write surface: " + ", ".join(extra)


# ═══ · gh ops pr-create and run-rerun ═════════════════════════════════════════════════════════════════════

def test_pr_create_dry_run_already_exists_and_verified_write():
    sha = "a" * 40
    ref_path = "/repos/octo/demo/git/ref/heads/feat%2Fx"
    pulls_path = "/repos/octo/demo/pulls"
    pr_path = "/repos/octo/demo/pulls/42"
    pr_obj = {
        "number": 42,
        "state": "open",
        "draft": False,
        "merged": False,
        "title": "New feature",
        "html_url": "https://github.com/octo/demo/pull/42",
        "head": {"ref": "feat/x", "sha": sha},
        "base": {"ref": "main"},
        "updated_at": "2026-04-17T00:00:00Z",
    }
    # 1. Dry-run when no open PR exists
    client, stub = client_for({
        ("GET", ref_path): reply({"object": {"sha": sha}}),
        ("GET", pulls_path): reply([]),
    })
    plan = gh_ops.pr_create(REPO_gh_ops_pr_edit_body, head="feat/x", base="main", title="New feature", body="Body", client=client)
    assert plan["ok"] and plan["dry_run"] and plan["status"] == "planned" and plan["head_sha"] == sha
    assert stub.methods == ["GET", "GET"]

    # 2. Idempotent when open PR already exists for (head, base)
    client, stub = client_for({
        ("GET", ref_path): reply({"object": {"sha": sha}}),
        ("GET", pulls_path): reply([pr_obj]),
    })
    exists = gh_ops.pr_create(REPO_gh_ops_pr_edit_body, head="feat/x", base="main", title="New feature", write=True, client=client)
    assert exists["ok"] and exists["status"] == "already_exists" and exists["number"] == 42 and not exists["created"]
    assert stub.methods == ["GET", "GET"]

    # 3. Write creates and verifies via readback
    client, stub = client_for({
        ("GET", ref_path): [reply({"object": {"sha": sha}}), reply({"object": {"sha": sha}})],
        ("GET", pulls_path): reply([]),
        ("POST", pulls_path): reply({"number": 42, "html_url": pr_obj["html_url"]}, status=201),
        ("GET", pr_path): reply(pr_obj),
    })
    created = gh_ops.pr_create(REPO_gh_ops_pr_edit_body, head="feat/x", base="main", title="New feature",
                               body="Body", expect_head_sha=sha[:12], write=True, client=client)
    assert created["ok"] and created["created"] and created["verified"] and created["status"] == "verified"
    assert created["number"] == 42
    assert stub.methods == ["GET", "GET", "GET", "POST", "GET"]


def test_pr_create_refuses_stale_head_or_head_mismatch():
    sha1 = "a" * 40
    sha2 = "b" * 40
    ref_path = "/repos/octo/demo/git/ref/heads/feat%2Fx"
    pulls_path = "/repos/octo/demo/pulls"
    client, stub = client_for({
        ("GET", ref_path): reply({"object": {"sha": sha1}}),
    })
    mismatch = gh_ops.pr_create(REPO_gh_ops_pr_edit_body, head="feat/x", title="T", expect_head_sha="cccc", write=True, client=client)
    assert not mismatch["ok"] and mismatch["status"] == "head_mismatch"
    assert stub.methods == ["GET"]

    client, stub = client_for({
        ("GET", ref_path): [reply({"object": {"sha": sha1}}), reply({"object": {"sha": sha2}})],
        ("GET", pulls_path): reply([]),
    })
    stale = gh_ops.pr_create(REPO_gh_ops_pr_edit_body, head="feat/x", title="T", write=True, client=client)
    assert not stale["ok"] and stale["status"] == "stale"
    assert stub.methods == ["GET", "GET", "GET"]


def test_run_rerun_preconditions_plan_and_write():
    sha = "c" * 40
    run_path = "/repos/octo/demo/actions/runs/99"
    rerun_failed_path = "/repos/octo/demo/actions/runs/99/rerun-failed-jobs"
    rerun_all_path = "/repos/octo/demo/actions/runs/99/rerun"

    # Refuses non-completed or already successful runs
    for payload, expected_status in (
        ({"status": "in_progress", "conclusion": None, "head_sha": sha}, "not_completed"),
        ({"status": "completed", "conclusion": "success", "head_sha": sha}, "already_succeeded"),
    ):
        client, stub = client_for({("GET", run_path): reply(payload)})
        res = gh_ops.run_rerun(REPO_gh_ops_pr_edit_body, 99, write=True, client=client)
        assert not res["ok"] and res["status"] == expected_status
        assert stub.methods == ["GET"]

    # Dry-run plan for failed jobs
    failed_run = {"status": "completed", "conclusion": "failure", "head_sha": sha}
    client, stub = client_for({("GET", run_path): reply(failed_run)})
    plan = gh_ops.run_rerun(REPO_gh_ops_pr_edit_body, 99, expect_head_sha=sha[:8], client=client)
    assert plan["ok"] and plan["dry_run"] and plan["status"] == "planned"
    assert plan["would_post"]["path"] == rerun_failed_path

    # Write rerun-failed-jobs and rerun all
    client, stub = client_for({
        ("GET", run_path): reply(failed_run),
        ("POST", rerun_failed_path): reply({}, status=201),
    })
    done = gh_ops.run_rerun(REPO_gh_ops_pr_edit_body, 99, write=True, client=client)
    assert done["ok"] and done["rerun"] and done["status"] == "requested"
    assert stub.methods == ["GET", "POST"]

    client, stub = client_for({
        ("GET", run_path): reply(failed_run),
        ("POST", rerun_all_path): reply({}, status=201),
    })
    done_all = gh_ops.run_rerun(REPO_gh_ops_pr_edit_body, 99, failed_only=False, write=True, client=client)
    assert done_all["ok"] and done_all["rerun"] and not done_all["failed_only"]
    assert stub.methods == ["GET", "POST"]

