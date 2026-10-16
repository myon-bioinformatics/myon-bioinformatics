"""parent_pin_check positive and negative cases (offline + stubbed API)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tools.parent_pin_check import (
    PARENT_PIN_TOOL_PATH,
    PARENT_REPOSITORY,
    ParentPinError,
    audit_fleet_child,
    evaluate,
    fetch_parent_main_sha,
    main,
    pin_behind_main,
    run,
    run_fleet,
    scan,
    scan_text_files,
    should_enforce,
)


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def test_unified_pins_are_ok(tmp_path: Path) -> None:
    sha = "a" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{sha}
        """,
    )
    _write(
        tmp_path,
        "docs/guide.md",
        f"Pin: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{sha}\n",
    )
    pins = scan(tmp_path, include_default_excludes=False)
    report = evaluate(
        pins,
        parent_repository=PARENT_REPOSITORY,
        main_sha=sha,
        token=None,
        enforce=False,
    )
    assert report["ok"]
    assert not report["inconsistent"]
    assert not report["stale"]


def test_one_old_pin_fails_unification_when_enforced(tmp_path: Path) -> None:
    good = "b" * 40
    bad = "c" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          a:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{good}
          b:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-pages-static.yml@{bad}
        """,
    )
    pins = scan(tmp_path, include_default_excludes=False)
    report = evaluate(
        pins,
        parent_repository=PARENT_REPOSITORY,
        main_sha=good,
        token=None,
        enforce=True,
    )
    assert not report["ok"]
    assert report["inconsistent"]


def test_one_old_pin_warns_without_enforce(tmp_path: Path) -> None:
    good = "b" * 40
    bad = "c" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          a:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{good}
          b:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-pages-static.yml@{bad}
        """,
    )
    pins = scan(tmp_path, include_default_excludes=False)
    report = evaluate(
        pins,
        parent_repository=PARENT_REPOSITORY,
        main_sha=good,
        token=None,
        enforce=False,
    )
    assert report["ok"]
    assert report["inconsistent"]


def test_docs_only_old_pin_fails_when_enforced(tmp_path: Path) -> None:
    good = "d" * 40
    bad = "e" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{good}
        """,
    )
    _write(
        tmp_path,
        "docs/upgrade.md",
        f"""
        After merge, repin to
        {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{bad}
        """,
    )
    pins = scan(tmp_path, include_default_excludes=False)
    report = evaluate(
        pins,
        parent_repository=PARENT_REPOSITORY,
        main_sha=good,
        token=None,
        enforce=True,
    )
    assert not report["ok"]


def test_non_parent_sha_is_ignored(tmp_path: Path) -> None:
    parent_sha = "f" * 40
    other_sha = "1" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{parent_sha}
          probe:
            uses: myon-bioinformatics/xprobe/.github/workflows/x.yml@{other_sha}
        """,
    )
    _write(
        tmp_path,
        "vendor.lock.json",
        json.dumps(
            {
                "schema": "vendor-lock/1",
                "files": [
                    {
                        "repository": "myon-bioinformatics/gh_identity",
                        "commit": other_sha,
                        "destination": "vendor/gh_identity.py",
                    }
                ],
            }
        ),
    )
    pins = scan(tmp_path, include_default_excludes=False)
    commits = {pin["commit"] for pin in pins}
    assert commits == {parent_sha}


def test_parent_pr_ref_json_ignored_even_with_parent_commit(tmp_path: Path) -> None:
    workflow_sha = "2" * 40
    ref_sha = "3" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{workflow_sha}
        """,
    )
    _write(
        tmp_path,
        "templates/parent-pr.ref.json",
        json.dumps(
            {
                "schema": "parent-pr-ref/1",
                "repository": PARENT_REPOSITORY,
                "pull_number": 1,
                "repository_id": 1,
                "commit": ref_sha,
            }
        ),
    )
    pins = scan(tmp_path, include_default_excludes=True)
    commits = {pin["commit"] for pin in pins}
    assert commits == {workflow_sha}


def test_parent_pr_ref_missing_is_ok(tmp_path: Path) -> None:
    sha = "2" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{sha}
        """,
    )
    pins = scan(tmp_path, include_default_excludes=True)
    report = evaluate(
        pins,
        parent_repository=PARENT_REPOSITORY,
        main_sha=sha,
        token=None,
        enforce=True,
    )
    assert report["ok"]


def test_parent_pr_ref_without_commit_field_is_ok(tmp_path: Path) -> None:
    sha = "2" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{sha}
        """,
    )
    _write(
        tmp_path,
        "parent-pr.ref.json",
        json.dumps(
            {
                "schema": "parent-pr-ref/1",
                "repository": PARENT_REPOSITORY,
                "pull_number": 1,
                "repository_id": 1,
            }
        ),
    )
    pins = scan(tmp_path, include_default_excludes=True)
    assert pins
    assert all(pin["commit"] == sha for pin in pins)


def test_fetch_parent_main_from_atom(monkeypatch: pytest.MonkeyPatch) -> None:
    atom = """<?xml version="1.0"?>
    <feed><entry>
      <id>tag:github.com,2008:Commit/ece241afedb60d5daf0b12e37241ebb1dd001bd8</id>
    </entry></feed>"""

    import tools.parent_pin_check as module

    def fake_http_get_text(url, *, token=None, **_kwargs):
        assert token is None
        return atom

    monkeypatch.setattr(module, "_http_get_text", fake_http_get_text)
    sha, branch = fetch_parent_main_sha(PARENT_REPOSITORY, "token")
    assert branch == "main"
    assert sha == "ece241afedb60d5daf0b12e37241ebb1dd001bd8"


def test_child_heads_excluded_by_default(tmp_path: Path) -> None:
    sha = "2" * 40
    _write(
        tmp_path,
        "child-heads.json",
        json.dumps(
            {
                "heads": {
                    PARENT_REPOSITORY: {"sha": sha, "repository_id": 1},
                }
            }
        ),
    )
    pins = scan(tmp_path, include_default_excludes=True)
    assert pins == []


def test_stale_only_warns_by_default(tmp_path: Path) -> None:
    pin = "3" * 40
    main = "4" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{pin}
        """,
    )
    pins = scan(tmp_path, include_default_excludes=False)

    def fake_behind(*_args, **_kwargs):
        return True

    import tools.parent_pin_check as module

    original = module.pin_behind_main
    module.pin_behind_main = fake_behind
    try:
        report = evaluate(
            pins,
            parent_repository=PARENT_REPOSITORY,
            main_sha=main,
            token="token",
            enforce=False,
        )
        assert report["stale"]
        assert report["ok"]
        report_strict = evaluate(
            pins,
            parent_repository=PARENT_REPOSITORY,
            main_sha=main,
            token="token",
            enforce=True,
        )
        assert not report_strict["ok"]
    finally:
        module.pin_behind_main = original


def test_pin_constant_detected(tmp_path: Path) -> None:
    sha = "5" * 40
    _write(
        tmp_path,
        "settings.py",
        f'VENDOR_SYNC_PIN = "{sha}"\n',
    )
    pins = scan(tmp_path, include_default_excludes=False)
    assert len(pins) == 1
    assert pins[0]["kind"] == "pin-constant"


def test_run_skip_api_offline(tmp_path: Path) -> None:
    sha = "6" * 40
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"""
        jobs:
          lint:
            uses: {PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@{sha}
        """,
    )
    report = run(tmp_path, skip_api=True, include_default_excludes=False)
    assert report["ok"]


def test_fetch_parent_main_atom_failure_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    def boom(*args, **kwargs):
        raise ParentPinError("missing", http_code=404)

    monkeypatch.setattr(module, "_http_get_text", boom)
    with pytest.raises(module.ParentPinError, match="missing"):
        fetch_parent_main_sha(PARENT_REPOSITORY, "token")


def test_scan_text_files_matches_local_scan(tmp_path: Path) -> None:
    sha = "7" * 40
    text = f"uses: {PARENT_REPOSITORY}/.github/workflows/x.yml@{sha}\n"
    files = {".github/workflows/ci.yml": text}
    assert scan_text_files(files)[0]["commit"] == sha


def test_fleet_child_stale_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    main = "8" * 40
    stale = "9" * 40
    workflow = f"uses: {PARENT_REPOSITORY}/.github/workflows/x.yml@{stale}\n"

    def fake_resolve(repository, token):
        assert repository == "myon-bioinformatics/Ironmate"
        return ("a" * 40, "main")

    def fake_fetch(repository, ref, token, **kwargs):
        return {".github/workflows/ci.yml": workflow}

    monkeypatch.setattr("tools.parent_pin_check._resolve_default_branch_head", fake_resolve)
    monkeypatch.setattr("tools.parent_pin_check.fetch_repository_pin_files", fake_fetch)
    monkeypatch.setattr("tools.parent_pin_check.pin_behind_main", lambda *args, **kwargs: True)

    item = audit_fleet_child(
        "myon-bioinformatics/Ironmate",
        parent_repository=PARENT_REPOSITORY,
        main_sha=main,
        token="token",
    )
    assert item is not None
    assert item["pins"][0]["reason"] == "stale"
    assert item["pins"][0]["file"] == ".github/workflows/ci.yml"


def test_fleet_child_private_after_list_is_skipped_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_fleet_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )

    def fail_child(*args, **kwargs):
        raise ParentPinError("repository is not public", http_code=403)

    monkeypatch.setattr("tools.parent_pin_check.audit_fleet_child", fail_child)
    report = run_fleet(token="token", enforce=True)
    assert report["ok"]
    assert report["skipped_repositories"] == 1
    assert report["audit_error_count"] == 0
    assert report["fetch_warning_count"] == 0


def test_fleet_child_auth_error_warns_even_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_fleet_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )

    def fail_child(*args, **kwargs):
        raise ParentPinError("forbidden", http_code=403)

    monkeypatch.setattr("tools.parent_pin_check.audit_fleet_child", fail_child)
    report = run_fleet(token="token", enforce=True)
    assert report["ok"]
    assert report["fetch_warning_count"] == 1
    assert not report["complete"] and report["audited_repositories"] == 0
    assert "403" in report["fetch_warnings"][0]["message"]
    assert "repository" not in report["fetch_warnings"][0]


def test_fleet_child_unclassified_error_fails_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_fleet_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )

    def fail_child(*args, **kwargs):
        raise ParentPinError("commit tree missing")

    monkeypatch.setattr("tools.parent_pin_check.audit_fleet_child", fail_child)
    report = run_fleet(token="token", enforce=False)
    assert report["ok"]
    assert report["audit_error_count"] == 1

    report_strict = run_fleet(token="token", enforce=True)
    assert not report_strict["ok"]
    assert report_strict["audit_error_count"] == 1
    assert not report_strict["complete"] and report_strict["audited_repositories"] == 0


def test_should_enforce_event_policy() -> None:
    assert should_enforce(event_name="schedule", ref="refs/heads/main")
    assert should_enforce(event_name="schedule", ref="refs/heads/other")
    assert should_enforce(event_name="push", ref="refs/heads/main")
    assert not should_enforce(event_name="push", ref="refs/heads/feature/x")
    assert not should_enforce(event_name="pull_request", ref="refs/pull/85/merge")
    assert not should_enforce(event_name="workflow_dispatch", ref="refs/heads/main")


def test_fleet_warn_only_on_pull_request_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_fleet_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )
    monkeypatch.setattr(
        "tools.parent_pin_check.audit_fleet_child",
        lambda *args, **kwargs: {
            "repository": "myon-bioinformatics/Ironmate",
            "revision": "b" * 40,
            "pins": [{"file": ".github/workflows/ci.yml", "line": 1, "commit": "c" * 40, "kind": "workflow-uses", "reason": "stale"}],
            "inconsistent": False,
            "stale": True,
        },
    )
    report = run_fleet(token="token", enforce=False)
    assert report["failure_count"] == 1
    assert report["ok"]


def test_fleet_enforces_on_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_fleet_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )
    monkeypatch.setattr(
        "tools.parent_pin_check.audit_fleet_child",
        lambda *args, **kwargs: {
            "repository": "myon-bioinformatics/Ironmate",
            "revision": "b" * 40,
            "pins": [{"file": ".github/workflows/ci.yml", "line": 1, "commit": "c" * 40, "kind": "workflow-uses", "reason": "stale"}],
            "inconsistent": False,
            "stale": True,
        },
    )
    report = run_fleet(token="token", enforce=True)
    assert not report["ok"]


def test_compare_stale_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_json(url, token):
        return {"status": "behind", "ahead_by": 2, "behind_by": 0}

    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_github_json", fake_json)
    assert pin_behind_main(PARENT_REPOSITORY, "a" * 40, "b" * 40, "token")


def test_compare_unexpected_status_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    module._COMPARE_CACHE.clear()
    monkeypatch.setattr(module, "_github_json", lambda *args, **kwargs: {"status": "unknown"})
    with pytest.raises(ParentPinError, match="Unexpected GitHub compare status"):
        pin_behind_main(PARENT_REPOSITORY, "e" * 40, "f" * 40, "token")


def test_compare_zero_ahead_behind_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    module._COMPARE_CACHE.clear()
    monkeypatch.setattr(
        module,
        "_github_json",
        lambda *args, **kwargs: {"status": "behind", "ahead_by": 0, "behind_by": 0},
    )
    with pytest.raises(ParentPinError, match="zero ahead_by and behind_by"):
        pin_behind_main(PARENT_REPOSITORY, "e" * 40, "f" * 40, "token")


def test_fetch_repository_file_uses_http_404(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    def boom(*args, **kwargs):
        raise ParentPinError("missing", http_code=404)

    monkeypatch.setattr(module, "_http_get_text", boom)
    assert module._fetch_repository_file("myon-bioinformatics/x", "missing.yml", "a" * 40, None) is None


def _fake_pin_report(*, enforce: bool, stale: bool = True) -> dict:
    return {
        "schema": "parent-pin-check/1",
        "parent_repository": PARENT_REPOSITORY,
        "main_sha": "4" * 40,
        "pins": [],
        "unique_commits": ["3" * 40],
        "inconsistent": False,
        "stale": stale,
        "stale_commits": ["3" * 40] if stale else [],
        "enforce": enforce,
        "ok": not (enforce and stale),
    }


def test_main_exit_code_schedule_enforces_stale_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, enforce=False, **_kwargs):
        assert enforce is True
        return _fake_pin_report(enforce=True)

    monkeypatch.setattr("tools.parent_pin_check.run", fake_run)
    assert main(["--root", ".", "--event", "schedule", "--ref", "refs/heads/main"]) == 1


def test_main_exit_code_pull_request_warns_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, enforce=False, **_kwargs):
        assert enforce is False
        return _fake_pin_report(enforce=False)

    monkeypatch.setattr("tools.parent_pin_check.run", fake_run)
    assert (
        main(
            [
                "--root",
                ".",
                "--event",
                "pull_request",
                "--ref",
                "refs/pull/85/merge",
            ]
        )
        == 0
    )


def test_main_exit_code_non_main_push_warns_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, enforce=False, **_kwargs):
        assert enforce is False
        return _fake_pin_report(enforce=False)

    monkeypatch.setattr("tools.parent_pin_check.run", fake_run)
    assert (
        main(
            [
                "--root",
                ".",
                "--event",
                "push",
                "--ref",
                "refs/heads/feature/x",
            ]
        )
        == 0
    )


def test_main_exit_code_main_push_enforces(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, enforce=False, **_kwargs):
        assert enforce is True
        return _fake_pin_report(enforce=True)

    monkeypatch.setattr("tools.parent_pin_check.run", fake_run)
    assert main(["--root", ".", "--event", "push", "--ref", "refs/heads/main"]) == 1


def test_main_fleet_audit_error_enforces(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fleet(*_args, enforce=False, **_kwargs):
        assert enforce is True
        return {
            "schema": "parent-pin-fleet/1",
            "parent_repository": PARENT_REPOSITORY,
            "parent_main_sha": "a" * 40,
            "parent_default_branch": "main",
            "checked_repositories": 1,
            "failure_count": 0,
            "failures": [],
            "audit_error_count": 1,
            "audit_errors": [{"message": "repository is not public"}],
            "enforce": True,
            "ok": False,
        }

    monkeypatch.setattr("tools.parent_pin_check.run_fleet", fake_fleet)
    assert main(["--fleet", "--event", "schedule", "--ref", "refs/heads/main"]) == 1


def test_parent_pin_tool_path_constant() -> None:
    assert PARENT_PIN_TOOL_PATH == "tools/parent_pin_check.py"


def test_reusable_child_layout_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    consumer = tmp_path / "consumer"
    tool_root = tmp_path / "parent-pin-tool"
    sha = "a" * 40
    _write(
        consumer,
        ".github/workflows/ci.yml",
        f"uses: {PARENT_REPOSITORY}/.github/workflows/reusable-parent-pin-check.yml@{sha}\n",
    )
    tool_root.mkdir()
    tool_src = Path(__file__).resolve().parents[1] / PARENT_PIN_TOOL_PATH
    (tool_root / "tools").mkdir()
    (tool_root / PARENT_PIN_TOOL_PATH).write_text(
        tool_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: (sha, "main"),
    )
    code = main(
        [
            "--root",
            str(consumer),
            "--event",
            "pull_request",
            "--ref",
            "refs/pull/1/merge",
            "--skip-api",
        ]
    )
    assert code == 0


def _public_repo(name: str, **changes) -> dict:
    return {
        "full_name": name,
        "private": False,
        "visibility": "public",
        "archived": False,
        "default_branch": "main",
        **changes,
    }


@pytest.mark.parametrize("finding", ["stale", "mixed"])
def test_fleet_covers_seventeen_public_children_including_unregistered_xprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], finding: str,
) -> None:
    import tools.parent_pin_check as module

    # Reproduce the 16-child registry without tying this regression to future enrollment.
    names = [f"myon-bioinformatics/known-{n}" for n in range(16)]
    heads = {name: {"sha": "e" * 40} for name in [PARENT_REPOSITORY, *names]}
    xprobe = "myon-bioinformatics/xprobe"
    assert len(names) == 16 and xprobe not in names
    secret = "myon-bioinformatics/private-baseline"
    private_sha = "f" * 40
    heads[secret] = {"sha": private_sha}
    registry = tmp_path / "child-heads.json"
    registry.write_text(json.dumps({"heads": heads}), encoding="utf-8")

    def inventory(url, token):
        if "/orgs/" in url:
            raise ParentPinError("not an organization", http_code=404)
        return [
            _public_repo(name) for name in [PARENT_REPOSITORY, *names, xprobe]
        ] + [
            _public_repo(secret, private=True, visibility="private"),
            _public_repo("myon-bioinformatics/internal", visibility="internal"),
            _public_repo("myon-bioinformatics/archived", archived=True),
            _public_repo("another-owner/unrelated"),
        ]

    audited = []
    monkeypatch.setattr(module, "_github_json_list", inventory)
    monkeypatch.setattr(module, "fetch_parent_main_sha", lambda *a: ("a" * 40, "main"))
    monkeypatch.setattr(module, "_resolve_default_branch_head", lambda *a: ("b" * 40, "main"))
    monkeypatch.setattr(module, "pin_behind_main", lambda *a, **kw: True)

    def files(repository, revision, token, **kwargs):
        audited.append(repository)
        assert revision == "b" * 40  # Audit live HEAD, not the registry baseline.
        pins = ["a" * 40]
        if repository == xprobe:
            pins = ["c" * 40] if finding == "stale" else ["c" * 40, "d" * 40]
        return {".github/workflows/ci.yml": "\n".join(
            f"uses: {PARENT_REPOSITORY}/.github/workflows/x.yml@{pin}" for pin in pins
        )}

    monkeypatch.setattr(module, "fetch_repository_pin_files", files)
    report = run_fleet(child_heads=registry, token=None, enforce=True)
    assert len(audited) == len(set(audited)) == report["checked_repositories"] == 17
    assert set(audited) == {*names, xprobe}
    assert report["audited_repositories"] == 17 and report["complete"]
    assert report["registered_repositories"] == 16
    assert report["registry_missing_repositories"] == [xprobe]
    assert report["registry_missing_count"] == 1
    assert report["registry_unmatched_count"] == 1
    assert report["failures"][0]["repository"] == xprobe
    assert report["failures"][0]["inconsistent"] == (finding == "mixed")
    assert report["failures"][0]["stale"] == (finding == "stale")
    assert not report["ok"]
    module.emit_fleet_annotations(report)
    output = capsys.readouterr().out + module.render_fleet_text(report) + json.dumps(report)
    assert xprobe in output and "missing from child-heads.json" in output
    for hidden in (secret, private_sha, "myon-bioinformatics/internal", "myon-bioinformatics/archived"):
        assert hidden not in output


@pytest.mark.parametrize("http_code", [401, 403, 404, 500, None])
@pytest.mark.parametrize("event", ["schedule", "pull_request"])
def test_inventory_failure_never_falls_back_to_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], http_code: int | None, event: str,
) -> None:
    import tools.parent_pin_check as module

    registry = tmp_path / "child-heads.json"
    registry.write_text(json.dumps({"heads": {"myon-bioinformatics/known": {"sha": "a" * 40}}}))
    monkeypatch.setattr(module, "fetch_parent_main_sha", lambda *a: ("a" * 40, "main"))

    def unavailable(url, token):
        if "/orgs/" in url:
            raise ParentPinError("not an organization", http_code=404)
        # Server-provided details must not escape through the inventory error.
        raise ParentPinError("private-name private-sha", http_code=http_code)

    monkeypatch.setattr(module, "_github_json_list", unavailable)
    monkeypatch.setattr(module, "audit_fleet_child", lambda *a, **kw: pytest.fail("incomplete inventory was audited"))
    assert main(["--fleet", "--child-heads", str(registry), "--event", event]) == 2
    output = capsys.readouterr().err
    assert "incomplete fleet" in output and "private-name" not in output


def test_inventory_later_page_failure_does_not_return_partial_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    def pages(url, token):
        if "&page=1" in url:
            return [_public_repo(f"myon-bioinformatics/repo-{n}") for n in range(100)]
        raise ParentPinError("page unavailable", http_code=404)

    monkeypatch.setattr(module, "_github_json_list", pages)
    with pytest.raises(ParentPinError, match="incomplete fleet"):
        module._list_public_child_repositories("myon-bioinformatics", PARENT_REPOSITORY, None)


def test_inventory_includes_later_pages_and_deduplicates_case(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    def pages(url, token):
        if "&page=1" in url:
            return [_public_repo(f"myon-bioinformatics/repo-{n}") for n in range(100)]
        return [_public_repo("myon-bioinformatics/REPO-0"), _public_repo("myon-bioinformatics/xprobe")]

    monkeypatch.setattr(module, "_github_json_list", pages)
    names = module._list_public_child_repositories("myon-bioinformatics", PARENT_REPOSITORY, None)
    assert len(names) == 101 and "myon-bioinformatics/xprobe" in names


def test_inventory_pagination_limit_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_github_json_list", lambda *a: [_public_repo("myon-bioinformatics/repo")] * 100)
    with pytest.raises(ParentPinError, match="pagination limit"):
        module._list_public_child_repositories("myon-bioinformatics", PARENT_REPOSITORY, None)


@pytest.mark.parametrize("payload", [[None], [{}], [_public_repo("myon-bioinformatics/x", private=None)]])
def test_inventory_malformed_metadata_fails_closed(monkeypatch: pytest.MonkeyPatch, payload: list) -> None:
    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_github_json_list", lambda *a: payload)
    with pytest.raises(ParentPinError, match="metadata"):
        module._list_public_child_repositories("myon-bioinformatics", PARENT_REPOSITORY, None)


def test_inventory_auth_retry_reads_complete_public_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    calls = []
    def inventory(url, token):
        calls.append(token)
        if token:
            raise ParentPinError("token scope", http_code=403)
        return [_public_repo("myon-bioinformatics/xprobe")]

    monkeypatch.setattr(module, "_github_json_list", inventory)
    assert module._list_public_child_repositories("myon-bioinformatics", PARENT_REPOSITORY, "token") == ["myon-bioinformatics/xprobe"]
    assert calls == ["token", None]


@pytest.mark.parametrize("changes", [{"private": True, "visibility": "private"}, {"archived": True}])
def test_child_becoming_ineligible_does_not_leak_registry_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict,
) -> None:
    import tools.parent_pin_check as module

    name = "myon-bioinformatics/now-ineligible"
    registry = tmp_path / "child-heads.json"
    registry.write_text('{"heads": {}}')
    monkeypatch.setattr(module, "fetch_parent_main_sha", lambda *a: ("a" * 40, "main"))
    monkeypatch.setattr(module, "_list_public_child_repositories", lambda *a: [name])
    monkeypatch.setattr(module, "_github_json", lambda *a: _public_repo(name, **changes))
    monkeypatch.setattr(module, "_http_get_text", lambda *a, **kw: pytest.fail("excluded repository content was fetched"))
    report = run_fleet(child_heads=registry, enforce=True)
    assert report["ok"] and report["skipped_repositories"] == 1
    assert report["audited_repositories"] == 0 and report["audit_error_count"] == 0
    assert name not in json.dumps(report) + module.render_fleet_text(report)


def test_resolve_uses_actual_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    name = "myon-bioinformatics/xprobe"
    monkeypatch.setattr(module, "_github_json", lambda *a: _public_repo(name, default_branch="develop"))
    def atom(url, **kwargs):
        assert "/commits/develop.atom" in url
        return f"<feed><entry><id>Commit/{'a' * 40}</id></entry></feed>"
    monkeypatch.setattr(module, "_http_get_text", atom)
    assert module._resolve_default_branch_head(name, None) == ("a" * 40, "develop")


def test_invalid_inventory_json_is_a_safe_parent_pin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_http_get_bytes", lambda *a, **kw: b'{"private-name":')
    with pytest.raises(ParentPinError, match="invalid list payload"):
        module._github_json_list("https://api.github.com/users/owner/repos")


@pytest.mark.parametrize("code", [401, 403])
def test_public_api_auth_failure_retries_anonymously(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    import tools.parent_pin_check as module

    calls = []
    def request(url, *, token, **kwargs):
        calls.append(token)
        if token:
            raise ParentPinError("authentication failed", http_code=code)
        return b'{"status": "identical"}'
    monkeypatch.setattr(module, "_http_get_bytes", request)
    assert module._github_json("https://api.github.com/repos/public/repo", "token") == {"status": "identical"}
    assert calls == ["token", None]


def test_public_api_uses_token_for_head_tree_and_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.parent_pin_check as module

    calls = []
    def request(url, *, token, **kwargs):
        assert token == "token"
        calls.append(url)
        if "/compare/" in url:
            return b'{"status":"ahead", "ahead_by":1, "behind_by":0}'
        if "/commits/" in url:
            return b'{"commit":{"tree":{"sha":"tree"}}}'
        if "/git/trees/" in url:
            return b'{"truncated":false,"tree":[]}'
        return json.dumps(_public_repo("myon-bioinformatics/xprobe")).encode()
    monkeypatch.setattr(module, "_http_get_bytes", request)
    monkeypatch.setattr(module, "_http_get_text", lambda *a, **kw: f"<entry><id>Commit/{'a' * 40}</id></entry>")
    module._COMPARE_CACHE.clear()
    revision, _ = module._resolve_default_branch_head("myon-bioinformatics/xprobe", "token")
    assert module._remote_scannable_paths("myon-bioinformatics/xprobe", revision, "token", [], fleet=True) == []
    pins = [{"commit": "c" * 40, "file": "ci.yml", "line": 1}]
    assert evaluate(pins, parent_repository=PARENT_REPOSITORY, main_sha="a" * 40, token="token", enforce=True)["stale"]
    assert len(calls) == 4
