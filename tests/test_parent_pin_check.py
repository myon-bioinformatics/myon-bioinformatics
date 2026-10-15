"""parent_pin_check positive and negative cases (offline + stubbed API)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tools.parent_pin_check import (
    PARENT_REPOSITORY,
    ParentPinError,
    audit_fleet_child,
    evaluate,
    fetch_parent_main_sha,
    pin_behind_main,
    run,
    run_fleet,
    scan,
    scan_text_files,
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
        fail_on_stale=False,
    )
    assert report["ok"]
    assert not report["inconsistent"]
    assert not report["stale"]


def test_one_old_pin_fails_unification(tmp_path: Path) -> None:
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
        fail_on_stale=False,
    )
    assert not report["ok"]
    assert report["inconsistent"]


def test_docs_only_old_pin_fails(tmp_path: Path) -> None:
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
        fail_on_stale=False,
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
            fail_on_stale=False,
        )
        assert report["stale"]
        assert report["ok"]
        report_strict = evaluate(
            pins,
            parent_repository=PARENT_REPOSITORY,
            main_sha=main,
            token="token",
            fail_on_stale=True,
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


def test_fleet_skips_private_without_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.parent_pin_check.fetch_parent_main_sha",
        lambda *args, **kwargs: ("a" * 40, "main"),
    )
    monkeypatch.setattr(
        "tools.parent_pin_check._list_public_child_repositories",
        lambda *args, **kwargs: ["myon-bioinformatics/Ironmate"],
    )

    def fail_child(*args, **kwargs):
        raise ParentPinError("repository is not public")

    monkeypatch.setattr("tools.parent_pin_check.audit_fleet_child", fail_child)
    report = run_fleet(token="token")
    assert report["ok"]
    assert report["skipped_repositories"] == 1
    assert report["failure_count"] == 0


def test_compare_stale_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_json(url, token):
        return {"status": "behind", "ahead_by": 2, "behind_by": 0}

    import tools.parent_pin_check as module

    monkeypatch.setattr(module, "_github_json", fake_json)
    assert pin_behind_main(PARENT_REPOSITORY, "a" * 40, "b" * 40, "token")
