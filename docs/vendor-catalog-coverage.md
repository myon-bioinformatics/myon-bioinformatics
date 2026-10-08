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
