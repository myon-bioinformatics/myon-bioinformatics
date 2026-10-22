"""Existing PR consolidation without ad-hoc gh api scripts."""
import json

import pytest
from test_gh_ops import REPO, client_for, gh_ops, reply, pr_payload

PATH = '/repos/octo/demo/pulls/11'
BLOCK = '## Consolidated scope\n- #150 を取り込み 🐍'
BEFORE = pr_payload(title='Old', body='Existing\r\n日本語')
AFTER = pr_payload(title='Updated', body=BEFORE['body'] + '\n\n' + BLOCK)


def routes(gets, patch=None):
    return {('GET', PATH): [reply(x) for x in gets], ('PATCH', PATH): patch or reply({})}


def test_title_and_append_are_one_patch_and_verified():
    client, stub = client_for(routes([BEFORE, BEFORE, AFTER]))
    result = gh_ops.pr_edit(REPO, 11, title='Updated', append_body=BLOCK, write=True, client=client)
    assert result['ok'] and result['status'] == 'verified'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET']
    assert stub.calls[2]['body'] == {'title': 'Updated', 'body': AFTER['body']}


@pytest.mark.parametrize('old', [None, '', 'Existing'])
def test_append_dry_run_preserves_old_body(old):
    client, stub = client_for(routes([pr_payload(body=old)]))
    result = gh_ops.pr_edit(REPO, 11, append_body=BLOCK, client=client)
    assert result['status'] == 'planned' and stub.methods == ['GET']
    assert result['fields']['body']['to'] == (old + '\n\n' if old else '') + BLOCK


def test_retry_skips_duplicate_append_even_after_later_notes():
    client, stub = client_for(routes([{**AFTER, 'body': AFTER['body'] + '\n\nLater note'}]))
    result = gh_ops.pr_edit(REPO, 11, title='Updated', append_body=BLOCK, write=True, client=client)
    assert result['status'] == 'already_satisfied' and not result['changed']
    assert stub.methods == ['GET']


@pytest.mark.parametrize('change', [{'body': 'concurrent edit'}, {'head': {'sha': 'new'}}, {'title': 'concurrent title'}])
def test_append_refuses_observed_concurrent_edit(change):
    client, stub = client_for(routes([BEFORE, {**BEFORE, **change}]))
    result = gh_ops.pr_edit(REPO, 11, append_body=BLOCK, write=True, client=client)
    assert not result['ok'] and result['status'] == 'stale'
    assert stub.methods == ['GET', 'GET']


@pytest.mark.parametrize('http,status', [(403, 'mutation_failed'), (502, 'mutation_uncertain')])
def test_patch_errors_keep_distinct_receipts(http, status):
    client, stub = client_for(routes([BEFORE], reply({}, status=http)))
    result = gh_ops.pr_edit(REPO, 11, state='closed', write=True, client=client)
    assert not result['ok'] and result['status'] == status
    assert stub.methods == ['GET', 'PATCH']


def test_readback_failure_preserves_successful_mutation():
    client, stub = client_for({('GET', PATH): [reply(BEFORE), reply({}, status=503)],
                               ('PATCH', PATH): reply({})})
    result = gh_ops.pr_edit(REPO, 11, state='closed', write=True, client=client)
    assert result['status'] == 'verification_failed' and result['changed']
    assert result['mutation_status'] == 'succeeded' and not result['ok']
    assert stub.methods == ['GET', 'PATCH', 'GET']


@pytest.mark.parametrize('kwargs', [{'body': '', 'append_body': BLOCK}, {'append_body': ' \n'}, {'title': ''}])
def test_invalid_edit_before_request(kwargs):
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.pr_edit(REPO, 11, client=client, **kwargs)
    assert not stub.calls


def test_cli_file_preserves_unicode_and_real_crlf(tmp_path, capsys):
    path = tmp_path / 'body.md'
    body = '新しい本文\r\n\r\nlast\n'
    path.write_bytes(body.encode())
    client, stub = client_for(routes([BEFORE, {**BEFORE, 'body': body, 'title': 'Updated'}]))
    assert gh_ops.main(['--json', 'pr-edit', REPO, '11', '--title', 'Updated',
                        '--body-file', str(path), '--write'], client=client) == 0
    assert json.loads(capsys.readouterr().out)['verified']
    assert stub.calls[1]['body'] == {'title': 'Updated', 'body': body}


def test_cli_append_file(tmp_path, capsys):
    path = tmp_path / 'scope.md'
    path.write_text(BLOCK, encoding='utf-8')
    client, _ = client_for(routes([BEFORE]))
    assert gh_ops.main(['--json', 'pr-edit', REPO, '11', '--append-body-file', str(path)], client=client) == 0
    assert json.loads(capsys.readouterr().out)['fields']['body']['to'] == AFTER['body']
