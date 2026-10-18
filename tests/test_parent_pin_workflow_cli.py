"""Branch coverage for parent-pin-check workflow run parsing."""
from __future__ import annotations

import pytest

from tests.parent_pin_workflow_cli import cli_excludes_from_shell_run

PARENT_REPO_COMPAT_EXCLUDE = ".github/workflows/vendor-consumers-compat.yml"


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ("python tools/parent_pin_check.py --exclude 'tests/**'", ["tests/**"]),
        ('python tools/parent_pin_check.py --exclude "tests/**"', ["tests/**"]),
        ("python tools/parent_pin_check.py --exclude tests/**", ["tests/**"]),
        ("python tools/parent_pin_check.py --exclude=tests/**", ["tests/**"]),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f'  --exclude "{PARENT_REPO_COMPAT_EXCLUDE}"',
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f"  --exclude={PARENT_REPO_COMPAT_EXCLUDE}",
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
        (
            "python parent-pin-tool/tools/parent_pin_check.py \\\n"
            f"  --exclude {PARENT_REPO_COMPAT_EXCLUDE}",
            [PARENT_REPO_COMPAT_EXCLUDE],
        ),
    ],
)
def test_cli_excludes_from_shell_run_accepts_quote_styles(run: str, expected: list[str]) -> None:
    assert cli_excludes_from_shell_run(run) == expected


def test_cli_excludes_skips_blank_lines() -> None:
    run = "\n\npython tools/parent_pin_check.py --exclude 'only'\n  \n"
    assert cli_excludes_from_shell_run(run) == ["only"]


def test_cli_excludes_continues_command_across_blank_line_after_backslash() -> None:
    run = "python tools/parent_pin_check.py \\\n\n  --exclude 'joined'"
    assert cli_excludes_from_shell_run(run) == ["joined"]


def test_cli_excludes_flushes_segment_ending_with_trailing_backslash() -> None:
    run = "python tools/parent_pin_check.py --exclude 'tail' \\"
    assert cli_excludes_from_shell_run(run) == ["tail"]


def test_cli_excludes_returns_empty_when_no_parent_pin_invocation() -> None:
    assert cli_excludes_from_shell_run("set -euo pipefail\necho ok") == []


def test_cli_excludes_requires_value_for_bare_flag() -> None:
    with pytest.raises(ValueError, match="requires a value"):
        cli_excludes_from_shell_run("python tools/parent_pin_check.py --exclude")


def test_cli_excludes_collects_two_consecutive_bare_flags() -> None:
    run = "python tools/parent_pin_check.py --exclude 'a' --exclude 'b'"
    assert cli_excludes_from_shell_run(run) == ["a", "b"]


def test_cli_excludes_stops_at_first_parent_pin_segment() -> None:
    run = (
        "python tools/parent_pin_check.py --exclude 'first'\n"
        "python tools/parent_pin_check.py --exclude 'second'\n"
    )
    assert cli_excludes_from_shell_run(run) == ["first"]
