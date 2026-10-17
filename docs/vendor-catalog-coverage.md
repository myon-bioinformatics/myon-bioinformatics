# Organization-wide single-file catalog coverage

Surveyed 36 accessible repositories on 2026-10-08. Public results and selected
source/commit identities are in `vendor-target-inventory.json`; two private
repositories were inspected but their details are not published here.

Root-level standalone Python entrypoints are candidates even if the filename
needs a source override or the application has external dependencies. Multiple
root modules forming an application, test modules and vendored copies are not
independent shared tools. The parent repository's common CLI modules are included
with explicit source overrides. The inventory records repositories with no
standalone root tool rather than silently dropping them.

The catalog expands coverage to yourself, cli_args, markdown, ascii_artist,
nvd_nist_known_vulns and the parent's shared tools. Existing gh_identity/xprobe
recommended pins remain unchanged. Newly added entries select an exact inspected
default-branch SHA explicitly; this is not automatic development-head promotion.

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

2026-10-17 btk ownership (parent registry): `image_diff.py` is catalogued under
browser-test-kit with `skipped(requires_external_package)` and flutter-only
enrollment; parent-owned `py_matrix.py` and `pytest_brief.py` are registered
with explicit sources and `skipped(explicit_consumer_only)`.
