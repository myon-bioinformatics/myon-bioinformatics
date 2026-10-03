import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/reusable-vendor-update.yml"


def run(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("scenario", ["open", "closed", "remote_mismatch", "different_base", "different_candidate"])
def test_candidate_staging_and_pr_dedup_with_real_git(tmp_path, scenario):
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["propose"]["steps"]
    stage = next(s["run"] for s in steps if s.get("id") == "candidate")
    publish = next(s["run"] for s in steps if s.get("name") == "Open one PR for this candidate")
    origin = tmp_path / "origin.git"
    run(["git", "init", "--bare", str(origin)], tmp_path)
    seed = tmp_path / "seed"
    run(["git", "clone", str(origin), str(seed)], tmp_path)
    run(["git", "config", "user.name", "Fixture"], seed)
    run(["git", "config", "user.email", "fixture@example.invalid"], seed)
    run(["git", "checkout", "-b", "main"], seed)
    (seed / "vendor.lock.json").write_text('{}\n', encoding="utf-8")
    (seed / "adapter.py").write_text("old\n", encoding="utf-8")
    run(["git", "add", "."], seed)
    run(["git", "commit", "-m", "seed"], seed)
    run(["git", "push", "origin", "main"], seed)
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "gh-log"
    # Only PR API calls are substituted. Branch creation/staging/push use real Git.
    gh = binary / "gh"
    gh.write_text("#!/bin/sh\nif [ \"$1 $2\" = 'pr list' ]; then\n"
                  "  if [ \"$3 $4\" != '--state open' ]; then exit 3; fi\n"
                  "  if [ -f \"$GH_TEST_LOG\" ] && [ \"${GH_TEST_STATE:-}\" != closed ] && grep -F -- \"$6\" \"$GH_TEST_LOG\" >/dev/null; then echo 1; else echo 0; fi\n"
                  "elif [ \"$1 $2\" = 'pr create' ]; then\n"
                  "  printf '%s\\n' \"$*\" >> \"$GH_TEST_LOG\"\n"
                  "else exit 2; fi\n", encoding="utf-8")
    gh.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "GH_TEST_LOG": str(log), "MANIFEST": "vendor.lock.json", "BASE": "main"}
    branch = None
    for attempt in range(2):
        if attempt and scenario == "different_base":
            (seed / "ordinary.txt").write_text("base change\n", encoding="utf-8")
            run(["git", "add", "ordinary.txt"], seed)
            run(["git", "commit", "-m", "new base"], seed)
            run(["git", "push", "origin", "main"], seed)
        if attempt and scenario == "closed":
            env["GH_TEST_STATE"] = "closed"
        if attempt and scenario == "remote_mismatch":
            sabotage = tmp_path / "sabotage"
            run(["git", "clone", "--branch", branch, str(origin), str(sabotage)], tmp_path)
            run(["git", "config", "user.name", "Fixture"], sabotage)
            run(["git", "config", "user.email", "fixture@example.invalid"], sabotage)
            (sabotage / "adapter.py").write_text("unverified\n", encoding="utf-8")
            run(["git", "add", "adapter.py"], sabotage)
            run(["git", "commit", "-m", "different remote tree"], sabotage)
            run(["git", "push", "origin", branch], sabotage)
        checkout = tmp_path / f"checkout-{attempt}"
        run(["git", "clone", "--branch", "main", str(origin), str(checkout)], tmp_path)
        (checkout / "vendor.lock.json").write_text('{"candidate": "new"}\n', encoding="utf-8")
        candidate = "other candidate\n" if attempt and scenario == "different_candidate" else "new\n"
        (checkout / "adapter.py").write_text(candidate, encoding="utf-8")
        (checkout / "unrelated.txt").write_text("do not stage\n", encoding="utf-8")
        (checkout / ".vendor-update-result.json").write_text(
            json.dumps({"changed_paths": ["adapter.py", "vendor.lock.json"]}), encoding="utf-8")
        output = checkout / "outputs"
        env["GITHUB_OUTPUT"] = str(output)
        run(["bash", "-e", "-c", stage], checkout, env)
        values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
        assert values["changed"] == "true"
        env["BRANCH"] = values["branch"]
        if branch:
            assert (branch != values["branch"]) == (scenario in ("different_base", "different_candidate"))
        branch = values["branch"]
        if attempt and scenario == "remote_mismatch":
            result = subprocess.run(["bash", "-e", "-c", publish], cwd=checkout, env=env, capture_output=True, text=True)
            assert result.returncode == 1
            assert "does not match the verified candidate tree" in result.stderr
            assert len(log.read_text(encoding="utf-8").splitlines()) == 1
            assert run(["git", "show", branch + ":adapter.py"], origin) == "unverified"
            return
        run(["bash", "-e", "-c", publish], checkout, env)
        assert run(["git", "show", "--pretty=", "--name-only", "HEAD"], checkout).splitlines() == ["adapter.py", "vendor.lock.json"]
    assert len(log.read_text(encoding="utf-8").splitlines()) == (1 if scenario == "open" else 2)
    assert run(["git", "show", branch + ":adapter.py"], origin) == candidate.strip()
    assert len(run(["git", "rev-list", branch], origin).splitlines()) == (3 if scenario == "different_base" else 2)


def test_staging_no_change_is_no_proposal(tmp_path):
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    stage = next(s["run"] for s in workflow["jobs"]["propose"]["steps"] if s.get("id") == "candidate")
    run(["git", "init"], tmp_path)
    (tmp_path / ".vendor-update-result.json").write_text('{"changed_paths": []}', encoding="utf-8")
    output = tmp_path / "outputs"
    run(["bash", "-e", "-c", stage], tmp_path, {**os.environ, "GITHUB_OUTPUT": str(output)})
    assert output.read_text(encoding="utf-8") == "changed=false\n"
