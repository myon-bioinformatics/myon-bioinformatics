"""Parent exposes compatibility aliases, not a second Git implementation."""
import subprocess
import git_inspector as gi


def test_every_public_reader_delegates():
    for name in gi.__all__:
        assert getattr(gi, name) is getattr(gi._ghi, "git_" + name)
    assert gi.GitInspectionError is gi._ghi.GitInspectionError


def test_legacy_status_and_diff_work_without_mutation(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert gi.status(tmp_path) == {"clean": True, "records": [], "truncated": False}
    (tmp_path / "日本語.txt").write_text("text")
    assert not gi.status(tmp_path)["clean"]
    assert gi.diff(tmp_path)["truncated"] is False
    assert not (tmp_path / ".git/index").exists()
