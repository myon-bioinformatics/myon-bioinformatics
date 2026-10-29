# Reviewed parent observation baseline: 2026-10-29

This is a deliberate reconciliation of existing public, non-archived consumers,
not an automatic source release or a new repository enrollment. The baseline
starts from parent `c5ee5bd5dbdadf9c33ea0283fe4ae2725dc6c8f1`.

## Scope

- Refresh the 17 existing immutable observation anchors, including the parent.
- Register 17 already-adopted file destinations in 10 existing consumers. These
  are `py_launch.cjs` and its licenses, `pillow_min.py` and its licenses, and
  the portfolio’s `js/collection.js` copy. No child bytes are changed here.
- Preserve all adopted source commit/blob/SHA-256 values and the advisory catalog.
- Update Flutter’s explicitly approved full-workflow digest together with its
  observation anchor and the two existing pillow_min destinations.
- Remove stale optional PR numbers rather than attribute a new SHA to an old PR.

| Consumer | Recorded revision | Added registry entries |
| --- | --- | --- |
| myon-bioinformatics/Aoi | `0974142485dba4c236ae71b0d4e418cf3badd8bd` | 1 |
| myon-bioinformatics/Err-o-RoadRunner | `e3ffeb1ee896765c4da7d9915866a6b59014052c` | 0 |
| myon-bioinformatics/Ironmate | `ea7a552c316500a913e7b0eff84029a496d6ed1e` | 0 |
| myon-bioinformatics/ascii_artist | `aaef340ffa6c327cc31c7e7e09bac978fbe74933` | 1 |
| myon-bioinformatics/browser-test-kit | `6b02804d770bd4766c1159cc4c2b1231198bb90c` | 3 |
| myon-bioinformatics/cli_args | `a6bb795df12ec584dd0794b1d27980c5ecc142df` | 0 |
| myon-bioinformatics/flutter_navigation_basic | `5a647a52c96976b4318764fdaa4857c86d19cea7` | 2 |
| myon-bioinformatics/gh_identity | `46a7078040d672880dc2c786185723064694ee9a` | 1 |
| myon-bioinformatics/markdown | `45cdb78d36ba3e3259704652f1039cfab677ac67` | 0 |
| myon-bioinformatics/mcp-toolcall-lab | `3491298031f2b4c60bbba80ba191338e4f9fbe2d` | 0 |
| myon-bioinformatics/myon-bioinformatics | `c5ee5bd5dbdadf9c33ea0283fe4ae2725dc6c8f1` | 0 |
| myon-bioinformatics/myon-bioinformatics.github.io | `32d4ee3b8426a226836272efad8a5a593fe95ec6` | 3 |
| myon-bioinformatics/nvd_nist_known_vulns | `6c4b2beb0da23b115b50ec5246e91755ba03db30` | 2 |
| myon-bioinformatics/search_seq_including_spaces | `ffe4950256178402b203280fe3946d29cbe33033` | 0 |
| myon-bioinformatics/web-ui | `3d3f29147307797bd64bb2466ad5aa337fb057c4` | 1 |
| myon-bioinformatics/xprobe | `3c16f5cf1264812560d9198c28888925c6f585d6` | 1 |
| myon-bioinformatics/yourself | `9a4ffbc230a68805c35cc2bab0b97ad62b906800` | 2 |

## Existing workflow changes reviewed

The adopted interval includes workflow changes made by earlier child PRs. This
parent PR does not introduce or expand child workflow permissions.

- Flutter retains read-only permissions, a credentials-disabled fixed parent
  tool checkout, `check → remove → materialize → check`, metadata regressions,
  and artifact retention. Its reviewed full-file SHA-256 is
  `3871d074b2272dddca95987e08df7eebff6b61bf81f2655a6ee4dda86bd957e1`.
- gh_identity reports local vendor-hash findings on PR/manual/non-main runs;
  main push/schedule remains strict. Independent parent source/child blob
  verification remains required. This is not an enforced child PR merge gate.
- Aoi removed a nested BlueProbe lock check; the selected BlueProbe tree has no
  `vendor.lock.json`. Its shared-tool checkout resolves identity-checked main.
- BTK has same-repository PR reporting jobs with `pull-requests: write`.
- Portfolio Pages can run after a successful main `Update repos API` workflow,
  builds main, and records the actual checkout revision.
- web-ui includes the completed #97/#98 merges; the final #98 change replaces
  Node 24 with 26 in two test matrices and does not change its adopted lock.

## Verification and limits

The offline fixtures preserve the selected lock claims and prevent accidental
topology/head changes. They do not prove their own upstream provenance. That
requires independent regular-Git-blob reads, source first-parent reachability,
source blob/SHA-256 checks, and byte comparison with the recorded child revision.
The same source audit is required in parent CI.

Historical regression cases retain their historical registry/anchor subset. A
separate full-fleet regression checks the current 17 locks, removed/renamed
destinations, and exact anchors. Flutter’s entire fault matrix runs against both
the historical and current reviewed workflow.

An observation anchor is not a release target. Main branches may advance after
this snapshot, including the parent when this PR merges. Such subsequent drift
must remain visible; passing PR validation does not establish perpetual zero
drift, installed child merge gates, or successful execution of every child suite.
Unrelated parent experimental PR #175 is outside this change.
