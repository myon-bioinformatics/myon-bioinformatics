# Python runtime and CI setup policy

Runtime support and development tooling have separate floors. The parent vendor
utilities retain their Python 3.9 standard-library contract; `gh_workflow.py`
retains Python 3.10+. The `stdlib-runtime` jobs use `python -S` and `unittest`,
without installing third-party packages. They exercise offline locked vendor
materialization, tamper rejection, minpy bundle regeneration/execution, and
workflow-trigger parsing at the applicable floor.

Pytest/development jobs use Python 3.11 or newer. The main regression matrix runs
3.11–3.14; vendor portability also runs on Windows/macOS 3.12. Removing older
pytest jobs does not lower or replace any runtime contract. JUnit collection
names match these development matrices; stdlib smoke results are separate jobs.

Those pytest jobs install the `dev` group from `pyproject.toml` and invoke
`uv run pytest`. The group lists lower bounds only. The parent's `uv.lock` is committed and
pytest workflows set `UV_LOCKED=1`, so the lock is the explicit source of resolved
versions and uv fails rather than rewriting it. The Python 3.9
`node-launch-tests.yml` smoke lane is an explicit exception: it uses
`uv run --no-project` because the parent project requires >=3.11. In this
mode `UV_LOCKED` does not apply, so the lane explicitly selects
`pytest==8.4.2` (the last compatible major line). Its transitive tools are
not governed by the parent lock; it is a runtime-compatibility probe, not
a reproducibility claim for the parent development environment.
`requires-python` there is the
pytest job floor, not the 3.9 runtime floor of the stdlib tools. `pytest.ini`
puts the repository root on `pythonpath`, because `uv run pytest` starts the
pytest console script and does not insert the working directory the way
`python -m pytest` did. The dev group also includes `pip` so tools that resolve
requirements in the same interpreter, such as `pytest_brief.py`, still can.
`vendor_candidate.py` is started with `uv run python -S` so the pytest it
launches is the one in that environment. Stdlib unittest lanes do not call uv.

Active parent workflows pin current official setup releases by full commit SHA,
with a version comment: setup-python v7.0.0 and setup-node v7.1.0 (verified
2026-10-19), and setup-uv v10.3.2 (verified 2026-10-23). These actions use
Node 24 and require runner v2.327.1 or newer; these jobs use GitHub-hosted
runners. Future updates must verify official release tags rather than copy
older pins from neighboring workflows.

The distributable `templates/vendor-child-ci.workflow.yml` is intentionally
unchanged in this focused update. Its floating setup-python v7 currently
resolves to v7.0.0. Changing its bytes requires a coordinated review of enrolled
child workflow copies and recorded baselines, because parent CI enforces exact
copy identity. This change does not retarget child locks, reusable workflows,
or child-head snapshots and does not claim that template rollout is complete.

The end state is uv management for every Python tool, including stdlib-only
single-file tools. A standalone copy is not a reason to leave a tool off uv.
When a standalone file is wanted, copy the file from the GitHub repo.
`pilot/vendor_dispatch.py` reads `vendor-catalog.json` and, for an installable
repository root, runs `uv pip install` against the git URL at branch main.
Resolved commits are written back into that catalog's `commit` fields in the
CI workspace. Consumer repositories get no project file or committed lock from
this flow; the parent's own `uv.lock` above is separate.
Environments where uv does not apply (Python 3.9, `python -S`, uv missing)
skip with a reason. `vendor.lock.json` and `child-heads.json` stay pinned. See
`docs/uv-git-source-migration.md`. Child repositories and the child workflow
template are unchanged.
