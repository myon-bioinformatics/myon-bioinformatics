# One catalog JSON, one dispatcher

This is a parent-only pilot. `vendor-catalog.json` is the manifest for both
Python and Node entries. `vendor.lock.json`, `vendor/`, `vendor-consumers.json`,
and `child-heads.json` stay as they are. Child repositories are not edited.
`templates/vendor-child-ci.workflow.yml` is not edited. A green parent check
does not block a child merge and is not reviewer approval.

## What the driver reads

`pilot/vendor_dispatch.py` is standard library only. Each catalog tool already
has `repository`, `commit`, and `source`. The driver treats that as the GitHub
location: the repository at `--resolve-ref` (CI passes `main`) and the source
path. It does not add a manifest, a package directory, or a lockfile.

`vendor-catalog/1` and `vendor-lock/1` both reject extra keys. The catalog
cannot store a branch beside the commit, and it has no digest field. The
dynamic selector is the command flag. The stored record is the existing
`commit` field. Fetch digests are printed by the command. Enrolled digests stay
in `vendor.lock.json`, checked by `vendor_sync.py` as before.

## What it runs

Python, when this interpreter is 3.11 or newer, uv is on PATH, and the
repository root is an installable project with no third-party dependencies:
`uv pip install` of the git URL. The checkout is temporary. uv is not asked for
a lockfile. The resolved commit is read from the install metadata and, with
`--record`, written back to that tool's `commit`.

Today that install is `browser-test-kit` (`check_evidence`, `check_png`,
`jsonl_digest`, `image_diff`). The source path is a file inside the project.
It is not passed as a subdirectory fragment. A subdirectory fragment only
works when that directory itself contains a project file, and these paths do
not.

Node 24 or newer: the catalog's Node files have no `package.json`, or the
`package.json` is private (`browser-test-kit`). npm cannot take them as git
dependencies. The driver fetches the file and prints sha256.

Every other entry is `skip` with a reason. `python -S` and Python 3.9 do not
invoke uv. A missing `uv` or Node, Node older than 24, and an offline run say
so. They are not omitted.

## What does not fit a git URL

`uv pip install` of a git URL needs a project file in the cloned tree. It does
not install a single file. This pilot does not add project files.

`tool.uv package = false` marks a development project, not a distribution.
`uv pip install` of that git URL still attempts a build and fails (the parent
repository and `gh_identity` both do). The driver skips those trees. The
standalone file, including `python -S` copies such as `gh_identity.py`,
`vendor_sync.py`, and `py_launch.cjs`, stays on the vendor lock.

A project with third-party dependencies is skipped. `mcp-toolcall-lab` is a
real package and is outside this pilot for that reason.

Reviewed catalog pins in git are not moved by this pull request. CI `--record`
updates the workspace copy so the job can show the commits `main` resolved.
Committing those moving SHAs would be a separate baseline review. `uv.lock`
stays gitignored and is not the record.

## How a child would switch

Not done here. A child whose upstream already has an installable project can
point uv at that git URL and keep `vendor_sync.py` for every file that is not
such a project. Nothing in this pull request retargets a child lock or the
child workflow template.
