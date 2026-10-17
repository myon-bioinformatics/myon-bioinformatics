# Organization-wide single-file catalog coverage

Surveyed 36 accessible repositories on 2026-10-08. Public results and selected
source/commit identities are in `vendor-target-inventory.json`; two private
repositories were inspected but their details are not published here.

Root-level standalone Python entrypoints are candidates even if the filename
differs from the repository name (for example `web-ui` → `web_ui.py`) or the
application has external dependencies. Multiple root modules forming an
application, test modules and vendored copies are not independent shared tools.
The parent repository's common CLI modules are included with explicit `source`
paths. The inventory records repositories with no standalone root tool rather
than silently dropping them.

Every catalog tool must declare `source` explicitly. Repository names normalize
to a Python module stem by lowercasing and mapping `-` and `.` to `_`; normalized
name collisions between different repositories fail validation.

The catalog expands coverage to yourself, cli_args, markdown, ascii_artist,
nvd_nist_known_vulns, web-ui (`web_ui.py`), BlueProbe (`blueprobe.py`), and the
parent's shared tools. `web_ui.py` uses `skipped(pending_explicit_enrollment)`
by default; only consumers with an existing locked copy (Aoi and github.io) are
`enrolled` explicitly. `blueprobe.py` uses `skipped(requires_sibling_module)`;
Aoi is additionally `skipped(subtree_child)` because subtree sync owns that
layout. QueRyu (`queryu.py`) and PythDRagoras (`pythdragoras.py`) use
`skipped(requires_external_package)`. A warn-only check for legacy implicit
`source` defaults in child snapshots is deferred; use
`validate --allow-legacy-implicit-source` until 2027-04-01 instead. Existing
gh_identity/xprobe recommended pins remain unchanged. Newly added entries select
an exact inspected default-branch SHA explicitly; this is not automatic
development-head promotion.

`default_enrollment` is an optional `{enrollment, reason}` decision. A consumer
entry overrides it. Without either, the existing default enrollment applies.
Archived repositories use `skipped(archived_repository)`; external-library apps
use `skipped(requires_external_package)`; vendor_sync/catalog/stage remain
`skipped(parent_managed_tool)`. ci_status requires its sibling vendor/gh_identity layout and uses `skipped(requires_companion_tool)`. These files are discoverable targets, not promises
of portable execution. The inventory's import scan is static evidence, not an
exhaustive dependency or import-side-effect proof.

Verify source identity with `python -S vendor_catalog.py verify`; it delegates to
vendor_sync and never imports the sources. Compare an existing consumer lock with
`python -S vendor_catalog.py compare --lock <path> --consumer owner/repository`.
Neither command changes the lock or places a file. Catalog skips do not delete
already locked files. Consumer enrollment/promotion remains an explicit operation.

This PR registers organization-wide targets. It does not claim that every consumer
has already adopted them. Ironmate's separate test-only xprobe checkout remains
valid until an explicit locked enrollment change is made.

2026-10-17 gap fill: files that consumers already lock but that were absent from
the catalog are now listed with explicit `source` values, because the default
`<repository>.py` guess does not match them (web-ui `web_ui.py`, xprobe
`scripts/xprobe_pytest.py`, browser-test-kit `scripts/check_evidence.py`,
`scripts/jsonl_digest.py`, `scripts/check_png.py`, mcp-toolcall-lab
`src/mcp_toolcall_lab/github_public_resolver.py` and
`src/mcp_toolcall_lab/adapters/html_snapshot.py`). Each pin is the commit locked by
the most consumers registered in `vendor-consumers.json`. They use
`default_enrollment: skipped(explicit_consumer_only)` with explicit `enrolled`
overrides for the repositories that already lock them, so no new registration is
requested from other consumers.

2026-10-17 btk ownership (parent registry): `image_diff.py` and
`scripts/interaction.mjs` are catalogued under browser-test-kit with explicit
sources (image_diff: `skipped(requires_external_package)`, flutter only;
interaction.mjs: `skipped(explicit_consumer_only)`, flutter only). The new parent-owned tools are staged as described below; no fleet-wide enrollment is requested.

### Staged acceptance of the new parent tools

PR #96 introduces `py_matrix.py`, `pytest_brief.py`, and `publish_check.py` without recommending an unmerged intermediate commit. PR #95 adds their catalog entries only after #96 merges, using its full first-parent main merge SHA. None has registered consumers yet. Flutter #197 keeps its reviewed parent `gh_ops.py` pin `1eda992aa6fa31a902e2c06428e53ab17e8fe3c1`; a later adoption of the new git-commit implementation must verify and materialize the accepted parent merge commit, never the PR head.
