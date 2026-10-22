"""Stateful offline API: coordinated commits, PR reuse and source closure ordering."""
import base64
import copy
import json
import urllib.parse

import pytest
from test_gh_ops import gh_ops


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
