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

The generator metadata `web_ui <commit>` and GIF receipt key `web_ui` remain
wire contracts. PSWB still requires Python 3.11+, while MCP already required
Python 3.12+. This reconciliation changes neither runtime floor nor Node 26.
