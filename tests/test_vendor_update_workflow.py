"""Public placement needs no authentication or repository write workflow."""
from pathlib import Path

import vendor_sync as sync


def test_public_fetch_does_not_use_environment_credentials(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == sync.MAX_BYTES + 1
            return b"public bytes"

    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 30
        return Response()

    monkeypatch.setenv("GH_TOKEN", "must-not-be-sent")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-sent-either")
    monkeypatch.setattr(sync, "urlopen", open_request)
    for url in ("https://api.github.com/repos/myon-bioinformatics/xprobe/commits?sha=main&per_page=1",
                "https://raw.githubusercontent.com/myon-bioinformatics/xprobe/" + "1" * 40 + "/xprobe.py"):
        assert sync._get(url) == b"public bytes"
    assert all(not request.has_header("Authorization") for request in requests)
    assert requests[0].get_header("Accept") == "application/vnd.github+json"


def test_repository_write_workflow_is_removed():
    root = Path(__file__).parents[1]
    assert not (root / ".github/workflows/reusable-vendor-update.yml").exists()
