"""variable-delete and issue-comment-upsert: writes live in gh_ops.py, not GHI."""
import pytest


from test_gh_ops import REPO, client_for, gh_ops, reply, pr_payload

VAR = "/repos/octo/demo/actions/variables/FOO"
VARIABLES = "/repos/octo/demo/actions/variables"
PR = "/repos/octo/demo/pulls/11"
COMMENTS = "/repos/octo/demo/issues/11/comments"
BOT = "github-actions[bot]"
MARK = "<!-- digest -->"


def test_variable_delete_dry_run_never_deletes():
    client, stub = client_for({("GET", VAR): reply({"name": "FOO", "value": "secret-ish"})})
    result = gh_ops.variable_delete(REPO, "FOO", client=client)
    assert result["ok"] and result["dry_run"] and result["status"] == "would_delete"
    assert stub.methods == ["GET"] and "value" not in str(result)


def test_variable_delete_verified_only_with_complete_scoped_listing():
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), reply({"message": "Not Found"}, status=404)],
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): reply({"total_count": 0, "variables": []}),
    })
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] and result["verified"] and result["status"] == "verified"
    assert stub.methods == ["GET", "DELETE", "GET", "GET"]


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


def test_variable_delete_404_before_requires_complete_listing():
    client, stub = client_for({
        ("GET", VAR): reply({"message": "Not Found"}, status=404),
        ("GET", VARIABLES): reply({"total_count": 0, "variables": []}),
    })
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
    assert result["ok"] and result["status"] == "already_absent" and stub.methods == ["GET", "GET"]


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
        ("GET", PR): reply(pr_payload()),
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


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
@pytest.mark.parametrize("after_write", [False, True])
def test_inaccessible_variable_list_never_proves_absence(status, after_write):
    missing = reply({"message": "Not Found"}, status=404)
    client, stub = client_for({
        ("GET", VAR): [reply({"name": "FOO"}), missing] if after_write else missing,
        ("DELETE", VAR): reply(None, status=204),
        ("GET", VARIABLES): reply({"message": "unavailable"}, status=status),
    })
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
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
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
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
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
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
        result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
        assert not result["ok"] and result["status"] == "verification_uncertain"


@pytest.mark.parametrize("status, uncertain", [(403, False), (500, True), (None, True)])
def test_variable_mutation_failure_preserves_uncertainty(status, uncertain):
    def failure(*unused):
        raise gh_ops.GhOpsError("failure", http_status=status)
    client, stub = client_for({("GET", VAR): reply({"name": "FOO"}), ("DELETE", VAR): failure})
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
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
    result = gh_ops.issue_comment_upsert(REPO, 11, body=MARK + " new", marker=MARK, author=BOT,
                                         write=True, client=client)
    assert not result["ok"] and not result["verified"]
    assert result["updated"] is True and result["mutation_status"] == "succeeded"
    assert result["status"] == "verification_uncertain" and result["comment_id"] == 2
    assert stub.methods.count("PATCH") == 1


def test_upsert_rejects_issue_target_before_mutation():
    client, stub = client_for({("GET", PR): reply({}, status=404)})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.issue_comment_upsert(REPO, 11, body=MARK + " new", marker=MARK, author=BOT,
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
    result = gh_ops.variable_delete(REPO, "FOO", write=True, client=client)
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
    result = gh_ops.issue_comment_upsert(REPO, 11, body=MARK + " new", marker=MARK, author=BOT,
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
        result = gh_ops.issue_comment_upsert(REPO, 11, body=body, marker=MARK, author=BOT, client=client)
        assert result["ok"] and result["dry_run"] and result["action"] == action
        assert stub.methods == ["GET", "GET"]
