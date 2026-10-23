# uv git sources beside the vendor lock

This is a parent-only pilot. `vendor.lock.json`, `vendor/`, `vendor-catalog.json`,
`vendor-consumers.json`, and `child-heads.json` stay as they are. Child
repositories are not edited. `templates/vendor-child-ci.workflow.yml` is not
edited. A green parent check does not block a child merge and is not reviewer
approval.

## Classes

**(a) Install from git today.** The file already lives in a project uv can
build (`browser-test-kit-tools`, `mcp-toolcall-lab`). A consumer names that
project and sets `branch = "main"`. The copied `vendor/` file is the old path.

**(b) Package the single file first.** Add a minimal project, keep the original
file runnable (`python gh_workflow.py`, including its current stdlib floor).
This repo does that for `gh_ops.py`, `gh_workflow.py`, and `node_launch.py`
under `packages/`. Each module entry is a symlink to the standalone file.
`gh_ops` also symlinks the locked `vendor/gh_identity.py` so import still finds
the adjacent copy. Other single-file Python tools stay in this class until
their own repositories gain a project. This PR does not add those projects.

**(c) Stay on the vendor lock.** Node (`.cjs`, `.mjs`, `.js`), CSS, JSON,
LICENSE files, and stdlib tools whose contract is a verbatim copy run with
Python 3.9 and/or `python -S`. That includes `vendor_sync.py`,
`vendor_catalog.py`, `vendor_stage.py`, `gh_identity.py`, `ci_status.py`,
`pillow_min.py`, and `py_launch.cjs`.

`child-heads.json` entries are pinned revision records, not installable
packages. Fixture locks under `tests/fixtures/` stay pinned.

## How a child would switch

Use a uv project with `requires-python >= 3.11` and lower bounds only. Do not
commit `uv.lock`. In CI, run `uv lock --upgrade` and record the commit from
the lock. Leave the source table on `branch = "main"` with no SHA.

Parent tool:

```toml
[project]
name = "example-child"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["gh-workflow"]

[tool.uv]
package = false

[tool.uv.sources]
gh-workflow = { git = "https://github.com/myon-bioinformatics/myon-bioinformatics", branch = "main", subdirectory = "packages/gh_workflow" }
```

Already packaged upstream:

```toml
[tool.uv.sources]
browser-test-kit-tools = { git = "https://github.com/myon-bioinformatics/browser-test-kit", branch = "main" }
```

`uv lock --upgrade` then `uv sync`. Import the installed module. Keep
`python <file>` where the child still needs the standalone entrypoint, and
keep a vendor-lock copy for every class (c) file. Pytest in this parent stays
`uv run pytest`.

## What this parent runs

`pilot/uv-upstream` installs `browser-test-kit-tools` from GitHub `main`.
`pilot/uv-parent` installs `gh-ops`, `gh-workflow`, and `node-launch` from
this repository's `main`. Until `packages/` is on `main`, the parent job
publishes the commit under test as a local `main` and resolves that. The
committed source table still says `branch = "main"`.

Resolved commits are printed and added to the job summary. They are not a new
file in git. The existing `vendor_sync.py check` job and the `python -S`
lanes are unchanged.

## Open decisions

- After this pilot is on `main`, the local `main` bootstrap should stop. Confirm the job then uses only GitHub `branch = "main"`.
- `gh-ops` embeds the locked `gh_identity.py` bytes from the same commit. A later `gh-identity` distribution would replace that symlink. The vendor lock remains the `python -S` enrollment.
- Class (a) installs the upstream project, not the single copied file. Import parity beyond `check_evidence`, `check_png`, and `jsonl_digest` is not claimed. `mcp-toolcall-lab` is class (a) but is not a pilot consumer (Python 3.12 and third-party dependencies).
- `browser-test-kit` still declares `requires-python >= 3.9`. This pilot's consumers declare `>=3.11` and do not change that repository.
- Resolved commits live in the CI log. A durable record, if wanted, is a separate decision. `uv.lock` stays gitignored.
- `packages/` uses git symlinks. The pilot job is Ubuntu. A Windows build of these projects needs a symlink checkout check.
