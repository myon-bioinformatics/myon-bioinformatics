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
    result = gh_ops.pr_edit(REPO, 11, append_body=block, write=True, client=client)
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
    result = gh_ops.pr_edit(REPO, 11, append_body=newline + block + newline, write=True, client=client)
    assert result['verified'] and stub.calls[2]['body'] == {'body': appended}
    retry = gh_ops.pr_edit(REPO, 11, append_body=block, write=True, client=client)
    assert retry['status'] == 'already_satisfied'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET', 'GET']


def test_concurrent_append_is_stale_then_retry_preserves_the_other_edit():
    original = pr_payload(body='foobar')
    concurrent = pr_payload(body='foobar\n\nSomeone else added this')
    after = pr_payload(body=concurrent['body'] + '\n\nfoo')
    client, stub = client_for(routes([original, concurrent, concurrent, concurrent, after, after]))
    first = gh_ops.pr_edit(REPO, 11, append_body='foo', write=True, client=client)
    assert first['status'] == 'stale' and stub.methods == ['GET', 'GET']
    assert gh_ops.pr_edit(REPO, 11, append_body='foo', write=True, client=client)['verified']
    assert gh_ops.pr_edit(REPO, 11, append_body='foo', write=True, client=client)['status'] == 'already_satisfied'
    assert [c['body'] for c in stub.calls if c['method'] == 'PATCH'] == [{'body': after['body']}]


def test_concurrent_change_at_readback_does_not_report_success():
    current = pr_payload(body='foobar')
    changed = pr_payload(body='foobar\n\nfoo\n\nConcurrent note')
    client, stub = client_for(routes([current, current, changed]))
    result = gh_ops.pr_edit(REPO, 11, append_body='foo', write=True, client=client)
    assert not result['ok'] and result['status'] == 'verification_mismatch'
    assert result['mutation_status'] == 'succeeded'
    assert stub.methods == ['GET', 'GET', 'PATCH', 'GET']
