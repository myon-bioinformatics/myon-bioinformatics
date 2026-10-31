"""scripts/check_action_uses.py がuses の SHA 固定方針を守る。"""
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_action_uses as uses  # noqa: E402

SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
OTHER = "1c37ad07a6a961277cf70c0d37d6f313000f5884"


def _write(root: Path, text: str) -> None:
    path = root / ".github" / "workflows" / "ci.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_live_workflows_match_parent_style():
    assert uses.style_problems(ROOT) == []


def test_tag_refs_are_rejected_for_all_actions(tmp_path):
    _write(
        tmp_path,
        "\n".join(
            [
                "jobs:",
                "  a:",
                "    steps:",
                f"      - uses: actions/checkout@{SHA}",
                "      - uses: actions/setup-python@v7",
                "      - uses: astral-sh/setup-uv@v10.3.0",
                "      - uses: actions/upload-artifact@v7.0.2",
                "      - uses: actions/checkout@v7 # movable",
                "",
            ]
        ),
    )
    found = uses.style_problems(tmp_path)
    assert len(found) == 5
    assert all("action は 40 桁 SHA と # vX.Y.Z" in item for item in found)


def test_parent_style_refs_pass(tmp_path):
    _write(
        tmp_path,
        "\n".join(
            [
                "jobs:",
                "  call:",
                "    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-actionlint.yml@"
                + SHA
                + " # actionlint v1.7.12",
                "  local:",
                "    steps:",
                f"      - uses: actions/checkout@{OTHER} # v7.0.1",
                f"      - uses: actions/cache/restore@{SHA} # v6.1.0",
                f"      - uses: actions/setup-python@{SHA} # v7.0.0",
                f"      - uses: astral-sh/setup-uv@{OTHER} # v10.3.0",
                "      - uses: ./.github/actions/local",
                "      - uses: docker://alpine:3",
                "",
            ]
        ),
    )
    assert uses.style_problems(tmp_path) == []


def test_hash_mismatch_warns_on_pull_request_and_fails_when_strict(tmp_path, capsys):
    _write(tmp_path, f"steps:\n  - uses: actions/setup-python@{SHA} # v7.0.0\n")

    def resolve(repository, tag):
        assert repository == "actions/setup-python"
        assert tag == "v7.0.0"
        return OTHER

    assert uses.hash_problems(tmp_path, resolve)[0].endswith(f"commit {OTHER} と違う")
    assert uses.main(["--root", str(tmp_path), "--check-hashes", "--warn"], resolve=resolve) == 0
    assert "::warning::" in capsys.readouterr().err
    assert uses.main(["--root", str(tmp_path), "--check-hashes"], resolve=resolve) == 1


def test_matching_release_hash_passes(tmp_path, capsys):
    _write(
        tmp_path,
        "\n".join(
            [
                f"      - uses: actions/setup-python@{SHA} # v7.0.0",
                f"      - uses: actions/setup-python@{SHA} # v7.0.0",
                f"      - uses: astral-sh/setup-uv@{OTHER} # v10.3.0",
                "",
            ]
        ),
    )
    calls = []

    def resolve(repository, tag):
        calls.append((repository, tag))
        return {"actions/setup-python": SHA, "astral-sh/setup-uv": OTHER}[repository]

    assert uses.main(["--root", str(tmp_path), "--check-hashes"], resolve=resolve) == 0
    assert calls == [
        ("actions/setup-python", "v7.0.0"),
        ("astral-sh/setup-uv", "v10.3.0"),
    ]
    assert "action uses ok" in capsys.readouterr().out


def test_annotated_tag_resolves_to_inner_commit(monkeypatch):
    seen = []

    def fake(url):
        seen.append(url)
        if url.endswith("/tags/v1"):
            return {"object": {"type": "tag", "sha": "b" * 40, "url": "https://example.test/tag"}}
        return {"object": {"type": "commit", "sha": "c" * 40}}

    monkeypatch.setattr(uses, "_github_json", fake)
    assert uses.github_tag_commit("anthropics/claude-code-action", "v1") == "c" * 40
    assert seen[0].endswith("/git/ref/tags/v1")


def test_api_failure_exits_two_even_when_warn(tmp_path, capsys):
    _write(tmp_path, f"steps:\n  - uses: actions/setup-python@{SHA} # v7.0.0\n")

    def resolve(repository, tag):
        raise uses.HashCheckError("GitHub API HTTP 403 for tags")

    assert uses.main(["--root", str(tmp_path), "--check-hashes"], resolve=resolve) == 2
    assert uses.main(["--root", str(tmp_path), "--check-hashes", "--warn"], resolve=resolve) == 2
    err = capsys.readouterr().err
    assert "::error::" in err
    assert err.count("::error::") == 2


def test_short_sha_is_rejected(tmp_path):
    _write(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA[:7]} # v7.0.1\n")
    found = uses.style_problems(tmp_path)
    assert len(found) == 1
    assert "40 桁 SHA" in found[0]


def test_third_party_tag_is_rejected(tmp_path):
    _write(tmp_path, "steps:\n  - uses: anthropics/claude-code-action@v1\n")
    assert len(uses.style_problems(tmp_path)) == 1


def test_third_party_sha_needs_version_comment(tmp_path):
    _write(tmp_path, f"steps:\n  - uses: example/action@{SHA}\n")
    assert len(uses.style_problems(tmp_path)) == 1


@pytest.mark.parametrize("key", ["uses", "'uses'", '"uses"', "uses "])
def test_quoted_block_keys_cannot_hide_tag_refs(tmp_path, key):
    _write(tmp_path, f"steps:\n  - {key}: actions/checkout@v7\n")
    assert len(uses.style_problems(tmp_path)) == 1
    _write(tmp_path, f"steps:\n  - {key}: actions/checkout@{SHA} # v7.0.1\n")
    assert uses.style_problems(tmp_path) == []


@pytest.mark.parametrize("key", ["uses", "'uses'", '"uses"'])
def test_flow_mapping_is_explicitly_rejected(tmp_path, key):
    _write(tmp_path, f"steps:\n  - {{{key}: actions/checkout@v7}}\n")
    found = uses.style_problems(tmp_path)
    assert len(found) == 1 and "flow mapping" in found[0]
    assert uses.main(["--root", str(tmp_path)]) == 1


@pytest.mark.parametrize("value", ["", ">-", "|"])
def test_empty_or_multiline_uses_is_not_silently_approved(tmp_path, value):
    _write(tmp_path, f"steps:\n  - uses: {value}\n      actions/checkout@v7\n")
    assert len(uses.style_problems(tmp_path)) == 1


def test_run_block_scalar_text_is_not_an_action(tmp_path):
    _write(tmp_path, "steps:\n  - run: |\n      echo '{uses: example/action@v1}'\n"
                    "      uses: example/documentation@v1\n"
                    "  - uses: actions/checkout@v7\n")
    found = uses.style_problems(tmp_path)
    assert len(found) == 1 and "checkout@v7" in found[0]


@pytest.mark.parametrize("header", ["name: |", "name: >-", "run: |"])
def test_sequence_scalar_does_not_hide_sibling_uses(tmp_path, header):
    _write(tmp_path, f"steps:\n  - {header}\n      example\n"
                    "    uses: actions/checkout@v7\n")
    found = uses.style_problems(tmp_path)
    assert len(found) == 1 and "checkout@v7" in found[0]


@pytest.mark.parametrize("text", [
    '  - name: "example {uses: example/action@v1}"\n',
    "  - name: 'example {uses: example/action@v1}'\n",
    "  - name: example {uses: example/action@v1}\n",
    "  - name: example # {uses: example/action@v1}\n",
    '  - {name: "example {uses: example/action@v1}", run: echo ok}\n',
    "  - {name: example, run: echo ok} # {uses: example/action@v1}\n",
])
def test_flow_looking_scalar_or_comment_is_not_an_action(tmp_path, text):
    _write(tmp_path, "steps:\n" + text)
    assert uses.style_problems(tmp_path) == []


def test_subactions_resolve_one_repository_tag(tmp_path):
    _write(tmp_path, f"steps:\n  - uses: actions/cache/restore@{SHA} # v6.1.0\n"
                    f"  - uses: actions/cache/save@{SHA} # v6.1.0\n")
    calls = []
    def resolve(repository, tag):
        calls.append((repository, tag))
        return SHA
    assert uses.hash_problems(tmp_path, resolve) == []
    assert calls == [("actions/cache", "v6.1.0")]


def test_existing_workflow_lint_lane_covers_all_workflow_changes_offline():
    import yaml  # Test dependency only; production scanner stays stdlib.
    workflow = yaml.load((ROOT / ".github/workflows/actionlint.yml").read_text(),
                         Loader=yaml.BaseLoader)
    for event in ("pull_request", "push"):
        paths = workflow["on"][event]["paths"]
        assert ".github/workflows/**" in paths
        assert "scripts/check_action_uses.py" in paths
        assert "tests/test_action_uses.py" in paths
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["action-uses"]
    assert job["steps"][-1]["run"] == "python3 -S scripts/check_action_uses.py"
    assert job["steps"][0]["with"]["persist-credentials"] == "false"
    assert "env" not in job and "permissions" not in job
