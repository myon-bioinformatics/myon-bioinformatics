# Reviewed child lock observations — 2026-11-03

This parent-only baseline update follows #202's deferred changed-lock review.
It updates four observation anchors; no child files, catalog recommendations,
registry topology, approved workflow digests or permissions are changed.

| Consumer | Selected main revision | Exact-head push CI evidence |
| --- | --- | --- |
| Aoi | `94fd7c8c6ccefd4a2e2227606a6f51ced36862a6` | [CI](https://github.com/myon-bioinformatics/Aoi/actions/runs/41401590978), shared lock, distributions, reports, links and action uses all success |
| cli_args | `5059570cdc0ceec33995aeca0eb521066db521e7` | [CLI](https://github.com/myon-bioinformatics/cli_args/actions/runs/41390950935), stdlib both success |
| web-ui | `a29a40ad123985a3dbb44837c68f98fd6ff9e8b2` | [Tooling](https://github.com/myon-bioinformatics/web-ui/actions/runs/41390085207), UI smoke, Gradio DOM and Pages all success |
| xprobe | `1ffbb7b984075c683690bbe4e9deb06199cdfe94` | [CI](https://github.com/myon-bioinformatics/xprobe/actions/runs/41390914849), stdlib both success |

## Lock/source review

Only parent launcher rows changed in these four locks: six rows in total.
The canonical source is parent main merge
`384f5dd532076062f7833038764e9a62322c387c`, verified on the actual parent
first-parent history. Node launcher changes raise Node's minimum from 24 to
26. The Python launcher additionally makes the usage example generic and adds
the explicit CLI entry/`--site` option; its existing library default remains.
These are reviewed source changes, not a metadata-only or compatibility-free
claim. The cited child CI results supply the consumer execution evidence.

Fresh public/non-archived repository records match all four recorded IDs.
All fifteen destination files at the immutable selected revisions are regular
Git blobs and match their lock blob/SHA-256. Every parent launcher copy also
matches `git show` of its exact canonical source. Other adopted rows are
unchanged from the previous recorded child locks. All four optional copied
child workflows remain absent, as previously unenforced; none is rolled out.
The new frozen fixture preserves the selected locks and revisions while old
fixtures remain historical evidence. Parent exact-head CI must still perform
independent complete source/first-parent verification, not trust this fixture.

## Remaining observations

Ironmate and browser-test-kit also update xprobe/markdown/cli_args; the portfolio
updates web-ui. Those component changes remain separate reviews. Do not promote
their observation anchors merely because their latest CI is green.

mcp-toolcall-lab currently pins node_launch.py to intermediate parent commit
`1f6f1e345f640827703e2872151a73b4cbdfd382`, absent from parent's first-parent
history. The destination bytes and digest are correct, but this provenance
violates the parent contract. [Lab PR #189](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/189)
changes only ref/commit to accepted merge 384f5dd (identical blob/bytes). The
parent lab observation stays unchanged until that repair is accepted and
re-observed. Ordinary child CI green is not sufficient provenance proof.

This is a finite reviewed snapshot. Later child or parent commits naturally
create new observation drift; no recursive repinning or automatic merge gate
is introduced. Continuing governance is tracked in parent issue #56.
