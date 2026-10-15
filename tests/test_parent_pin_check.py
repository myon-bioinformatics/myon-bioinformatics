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


def test_fetch_parent_main_requires_public(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_json(url, token):
        if url.endswith("/myon-bioinformatics/myon-bioinformatics"):
            return {"private": True, "visibility": "private"}
        raise AssertionError(url)

    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_github_json", fake_json)
    with pytest.raises(module.ParentPinError, match="not public"):
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
        "tools.parent_pin_check._list_public_child_repositories",
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
        "tools.parent_pin_check._list_public_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )

    def fail_child(*args, **kwargs):
        raise ParentPinError("forbidden", http_code=403)

    monkeypatch.setattr("tools.parent_pin_check.audit_fleet_child", fail_child)
    report = run_fleet(token="token", enforce=True)
    assert report["ok"]
    assert report["fetch_warning_count"] == 1
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
        "tools.parent_pin_check._list_public_child_repositories",
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
        "tools.parent_pin_check._list_public_child_repositories",
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
        "tools.parent_pin_check._list_public_child_repositories",
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

    monkeypatch.setattr(module, "_github_json", boom)
    assert module._fetch_repository_file("myon-bioinformatics/x", "missing.yml", "a" * 40, "t") is None


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
