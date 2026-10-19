"""variable-delete and issue-comment-upsert: writes live in gh_ops.py, not GHI."""
import pytest


from test_gh_ops import REPO, client_for, gh_ops, reply

VAR = "/repos/octo/demo/actions/variables/FOO"
COMMENTS = "/repos/octo/demo/issues/11/comments"
BOT = "github-actions[bot]"
MARK = "<!-- digest -->"


def test_variable_delete_dry_run_never_deletes():
    client, stub = client_for({("GET", VAR): reply({"name": "FOO", "value": "secret-ish"})})
    result = gh_ops.variable_delete(REPO, "FOO", client=client)
    assert result["ok"] and result["dry_run"] and result["status"] == "would_delete"
    assert stub.methods == ["GET"] and "value" not in str(result)


def test_variable_delete_verified_only_by_404_read_back():
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({"message": "Not Found"}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
    })
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] and result["verified"] and result["status"] == "verified"
    assert stub.methods == ["GET", "DELETE", "GET"]


def test_variable_delete_403_after_delete_is_uncertain_not_verified():
    client, _ = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({"message": "Forbidden"}, status=403)],
        ("DELETE", VAR): reply(None, status=204),
    })
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] is False and result["status"] == "verification_uncertain" and result["http_status"] == 403


def test_variable_delete_403_before_is_inaccessible_not_already_absent():
    client, stub = client_for({("GET", VAR): reply({"message": "Forbidden"}, status=403)})
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] is False and result["status"] == "inaccessible"
    assert stub.methods == ["GET"]


def test_variable_delete_404_before_is_already_absent():
    client, stub = client_for({("GET", VAR): reply({"message": "Not Found"}, status=404)})
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] and result["status"] == "already_absent" and stub.methods == ["GET"]


def test_variable_delete_rejects_bad_names_before_http():
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.variable_delete(REPO, "../x", client=client)
    assert stub.calls == []


def test_upsert_ignores_other_authors_and_patches_own_comment():
    comments = [
        {"id": 1, "user": {"login": "someone"}, "body": MARK + " theirs"},
        {"id": 2, "user": {"login": BOT}, "body": MARK + " old"},
    ]
    client, stub = client_for({
        ("GET", COMMENTS): reply(comments),
        ("PATCH", "/repos/octo/demo/issues/comments/2"): reply({"id": 2}),
        ("GET", "/repos/octo/demo/issues/comments/2"): reply({"id": 2, "body": MARK + " new"}),
    })
    result = gh_ops.issue_comment_upsert(REPO, 11, body=MARK + " new", marker=MARK, author=BOT,
                                         write=True, client=client)
    assert result["ok"] and result["action"] == "update" and result["comment_id"] == 2


def test_upsert_creates_when_only_other_authors_have_the_marker():
    from test_gh_ops import pr_payload
    client, stub = client_for({
        ("GET", COMMENTS): reply([{"id": 1, "user": {"login": "someone"}, "body": MARK}]),
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload()),
    })
    result = gh_ops.issue_comment_upsert(REPO, 11, body=MARK + " x", marker=MARK, author=BOT, client=client)
    assert result["ok"] and result["dry_run"] and result["action"] == "create"


def test_upsert_requires_marker_prefix():
    client, _ = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.issue_comment_upsert(REPO, 11, body="x", marker=MARK, author=BOT, client=client)
