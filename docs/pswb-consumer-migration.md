# PSWB canonical module consumer reconciliation

The desired registry follows the merged Aoi #188/#189, Portfolio #109 and
MCP #209 locks. Aoi no longer vendors PSWB and already adopted the common
`tools/uv_tool_build.py`. Portfolio generates browser assets from the pinned
package; `web-ui-assets.lock.json` owns those outputs, not the source-copy
registry. Its separately copied PSWB LICENSE remains registered. MCP copies
`pysaiyanweboo.py` and `pysaiyanweboo-LICENSE` as staged in parent #264.

The reviewed current locks and exact merge revisions are frozen in
`tests/fixtures/pswb_consumer_migration_20261113.json`. These are offline
regression inputs; independent source/placed-byte and first-parent audits
must verify the actual merged snapshots before PSWB #141 is merged.

`child-heads.json` remains the historical observation baseline. Exact old
Aoi and Portfolio observations retain their complete old topology through
the existing digest-bound transition mechanism, as MCP already does in
#264. No provenance, source-byte or child-byte check is skipped. A later
heads refresh must audit the new revision against the desired topology and
then remove its obsolete active transition. Historical fixtures remain.

On 2026-11-14, parent commit `878d5b2d3b950d0f74e0298c2302c2e2daf7f49f`
advanced Aoi's observation to `44a0a39a14e49ea938a3c83c8dbe4b3fba9e4143`
and enrolled seven stdlib copies. Its complete adopted lock is separately frozen
in `tests/fixtures/reviewed_aoi_lock_20261114.json`; the source and placed bytes,
Git blob hashes, SHA-256 hashes and source first-parent ancestry were checked at
the recorded revisions. The obsolete Aoi transition is retired rather than
retargeting its historical digest. Historical PSWB migration tests use the
registry, heads and transitions frozen together from parent commit
`04659e770560c83c2d3e830e803ba2fbebac04b0` in
`tests/fixtures/pswb_consumer_observation_context_20261113.json`.
The existing historical lock fixtures remain unchanged. Portfolio's active
transition remains in place because its observation has not been refreshed.

The generator metadata `web_ui <commit>` and GIF receipt key `web_ui` remain
wire contracts. PSWB still requires Python 3.11+, while MCP already required
Python 3.12+. This reconciliation changes neither runtime floor nor Node 26.
