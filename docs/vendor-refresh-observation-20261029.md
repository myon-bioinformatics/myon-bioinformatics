# Vendor refresh observation epoch: 2026-10-29

This reviewed snapshot records the parent plus eleven completed child vendor refreshes. It is not a distributor, release target, perpetual zero-drift promise, or child merge gate. No child workflow references are repinned.

## Immutable observations

| Repository | Revision | Vendor PR |
| --- | --- | --- |
| myon-bioinformatics/nvd_nist_known_vulns | `03db3fab37059c6ca993ff4c63061b2d8da63bde` | [#44](https://github.com/myon-bioinformatics/nvd_nist_known_vulns/pull/44) |
| myon-bioinformatics/ascii_artist | `3a427d5674c4da6d1a3be2e030d0cdc0ac7b16a3` | [#57](https://github.com/myon-bioinformatics/ascii_artist/pull/57) |
| myon-bioinformatics/markdown | `e8d97fdc21d16f2cf9581bcfbd41bb39ec446023` | [#130](https://github.com/myon-bioinformatics/markdown/pull/130) |
| myon-bioinformatics/web-ui | `267af7fd2d4968c3ea75e2df4e93ff7fb4414005` | [#100](https://github.com/myon-bioinformatics/web-ui/pull/100) |
| myon-bioinformatics/yourself | `35267f02fd7e649de8e0a9efdfdbcfe102fdc239` | [#50](https://github.com/myon-bioinformatics/yourself/pull/50) |
| myon-bioinformatics/search_seq_including_spaces | `5f4c57bfe8e43271ff16f01d3c1061d0b5991724` | [#53](https://github.com/myon-bioinformatics/search_seq_including_spaces/pull/53) |
| myon-bioinformatics/browser-test-kit | `31d6a94d24a976bd837c5a2feacd9a5271d8fee8` | [#130](https://github.com/myon-bioinformatics/browser-test-kit/pull/130) |
| myon-bioinformatics/Err-o-RoadRunner | `d37fb1d953fd5fda2d08371c403025f4681c216b` | [#49](https://github.com/myon-bioinformatics/Err-o-RoadRunner/pull/49) |
| myon-bioinformatics/Ironmate | `7adcb80de69a701a252a6a5abc7407be3f8132f5` | [#168](https://github.com/myon-bioinformatics/Ironmate/pull/168) |
| myon-bioinformatics/mcp-toolcall-lab | `5b28a92c0485451198cb76050a164f765388d609` | [#176](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/176) |
| myon-bioinformatics/myon-bioinformatics.github.io | `d780e20af30b44c86b66367afb6068187c82b117` | [#83](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/pull/83) |
| myon-bioinformatics/myon-bioinformatics | `96cb6b7bff461070d85668955c77a78462384588` | [#179 vendor update](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/179), then #178 installer fix |

## Finite source epoch and limits

- Thirty genuinely changed vendor-source rows were adopted across parent + eleven children. The 135 initially unchanged fleet rows were not cosmetically repinned. Source/license identity remains in each immutable adopted lock; this parent change updates observation anchors and offline fixtures only.
- Parent observation stops at `96cb6b7bff461070d85668955c77a78462384588`. This baseline itself will advance parent main: that expected self-drift is reported, not chased through another parent/child pin loop.
- Aoi (two changed vendor rows) and Flutter (six changed vendor rows) are **not refreshed** in this rollout. Their existing observation anchors, lock fixtures and Flutter approved full-workflow digest remain byte-for-byte unchanged. Pending permission/authorization gates are not treated as completion.
- Other source-only repositories retain their existing anchors. No repository is enrolled or removed.
- The prior parent baseline document remains historical. The current fleet fixture checks every selected SHA against its adopted immutable lock, not live main.

## Compatibility validation

All eleven child PRs passed their exact-head hosted workflows before normal merges, followed by successful exact-main workflows, including applicable browser, Docker/Deno, runtime matrices and existing Pages deployments. Local environment limitations and original failures remain in the PR/run history.
Portfolio #83 required a consumer-only compatibility fix: Pixiv/Wikipedia prose extracts use whitespace-preserving `div.ui-output` instead of code-semantic `pre`. Canonical shared vendor bytes and existing font/browser assertions stayed unchanged; actual Docker/desktop/mobile gates passed after the fix.

## Workflow review

All 67 workflow files across these eleven children have immutable revision SHA-256 evidence in the review packet. Thirty-four workflow paths differ from the previous parent anchors; none was added or removed. Every difference predates the vendor-refresh PRs; the vendor PRs contain zero workflow changes. Permissions and job environments remain unchanged.
Previously reviewed differences include immutable parent-tool pins, Python 3.9 runner compatibility, bounded Ironmate apt setup, and MCP portable summary runtime/export/browser checks. Web-ui Pages changes only its referenced commit while referenced workflow bytes stay identical.

## Evidence boundary

Offline fixtures are regression claims, not self-authenticating source provenance. Parent CI must independently read regular source Git blobs, verify first-parent reachability and lock SHA-1/SHA-256, and compare adopted child bytes at the recorded revisions. The final review packet contains local immutable child-byte attestations. A fresh local independent selected-source audit was attempted but produced only transient public-fetch retries before its execution session became unavailable; no terminal report was recovered. That fresh audit is inconclusive, not a passing provenance gate. Hosted parent CI must complete its independent source/child audit before this baseline can be accepted.
No automatic updater, extra permission, reviewer approval submission, new deployment trigger, or blanket promise that every optional/manual suite ran is introduced.
