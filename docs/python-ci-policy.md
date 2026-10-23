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
`uv run pytest`. The group lists lower bounds only. A generated `uv.lock` is
not part of the contract and is gitignored. `requires-python` there is the
pytest job floor, not the 3.9 runtime floor of the stdlib tools. `pytest.ini`
puts the repository root on `pythonpath`, because `uv run pytest` starts the
pytest console script and does not insert the working directory the way
`python -m pytest` did. Stdlib unittest lanes do not call uv.

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
