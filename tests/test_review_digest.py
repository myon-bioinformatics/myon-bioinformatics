"""Provenance and conservative resolution attribution for saved conversations."""
import json
import subprocess
import sys
from pathlib import Path

from scripts.review_digest import digest

HEAD = "a" * 40
OLD = "b" * 40


def comment(cid, body):
    return {"id": cid, "html_url": f"https://github.com/o/r/issues/3#issuecomment-{cid}", "body": body}


def test_attribution_links_exact_finding_and_keeps_recent_source():
    first = comment(10, f"from: gpt / to: maintainers\nhead: {OLD}\n## Blocking\n- fix src/x.py #31\n- fix #32")
    second = comment(20, f"from: dev\nto: reviewers\nhead: {HEAD}\nResolved: issuecomment-10[2] run/987 passed")
    a, b = digest([first, second], HEAD, keep=1)["comments"]
    assert (a["from"], a["to"], a["head"], a["covers"]) == ("gpt", "maintainers", OLD, [10])
    assert "src/x.py" in a["identifiers"] and "#31" in a["identifiers"]
    assert [f["severity"] for f in a["findings"]] == ["Blocking", "Blocking"]
    assert a["findings"][0]["reported_resolution"] is None
    assert a["findings"][1]["reported_resolution"] == {
        "covers": [20], "url": second["html_url"], "head": HEAD, "evidence": "run/987 passed"}
    assert "body" not in a and b["body"] == second["body"]


def test_old_head_quotes_examples_and_ambiguous_heads_do_not_resolve():
    mark = "解消: issuecomment-1[1] run/123 passed"
    bodies = [f"head: {OLD}\n{mark}", f"head: {HEAD[:8]}\n{mark}", f"head: {OLD}\nhead: {HEAD}\n{mark}",
              f"head: {HEAD}\n> {mark}", f"head: {HEAD}\n~~~~\n```\n{mark}\n~~~~",
              f"head: {HEAD}\n    {mark}", f"head: {HEAD}\n<!--\n{mark}\n-->",
              f"head: {HEAD}\n<blockquote>\n{mark}\n</blockquote>", f"head: {HEAD}\n`{mark}`", f"head: {HEAD}\n解消", f"head: {HEAD}\n{mark.replace('[1]', '[2]')}"]
    rows = [comment(1, "Should: reproduce")] + [comment(i, body) for i, body in enumerate(bodies, 2)]
    result = digest(rows, HEAD, keep=0)["comments"]
    assert result[0]["findings"][0]["reported_resolution"] is None
    assert all("body" not in row for row in result)


def test_cli_accepts_saved_raw_and_rejects_previews_and_duplicate_ids():
    path = Path(__file__).resolve().parents[1] / "scripts" / "review_digest.py"
    row = comment(5, "Nit: rename")
    for data, code in [({"comments": [row]}, 0), ([{"id": 5, "preview": "Nit: rename"}], 2), ([row, row], 2)]:
        result = subprocess.run([sys.executable, "-S", str(path), "-", "--head", HEAD],
                                input=json.dumps(data), text=True, capture_output=True)
        assert result.returncode == code
        if code == 0:
            assert json.loads(result.stdout)["comments"][0]["covers"] == [5]
