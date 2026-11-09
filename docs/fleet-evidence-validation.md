# 公開fleet / MCP検証（2026-11-09）

公開 https://myon-bioinformatics.github.io/myon-bioinformatics/fleet.jsonld を実際に取得し、mcp-toolcall-lab cd04472040f0db991c5138144c01f56a2b79e44b のFastMCP in-memory Clientからfleet_queryを実行した。遠隔MCPサーバーの疎通検証とは異なる。

19:49:08 JSTの公開グラフは親f789b06b1beb41360e8df163fbf4c19121da3495、検証中に公開された21:19:09 JST版はafa3c47322da65f1ff1fb1bdabc6a3ff27fd508d。後者はMCP planner採用を収録し、depsが17→18行になった。

|対象|deps|reverify影響先|再テスト行|再確認行|未登録行|
|---|---:|---:|---:|---:|---:|
|Aoi|26|10|44|200|5|
|Ironmate|67|8|24|67|6|
|mcp-toolcall-lab|18|6|16|34|3|

行数は推薦数であり、実行済みテスト数ではない。同一時点の比較は取得済みJSON-LDをplanner.query(document=...)へ渡して行った。whyはリポジトリ名だけでは0行だが、Aoi:vendor/py_launch.cjs、Ironmate:vendor/py_launch.cjs、mcp-toolcall-lab:vendor/vendor_fleet_plan.pyでは各1行の根拠を取得した。

## 独立照合

- AoiとIronmateのpy_launch.cjs、およびMCPのnode_launch.pyは親384f5dd532076062f7833038764e9a62322c387c固定。MCP plannerは親f789b06b1beb41360e8df163fbf4c19121da3495固定。4件とも子ファイルSHA-256・lock blob・親の固定先source blobが一致し、両pinは親first-parent履歴に存在した。古いpin自体は不正ではない。
- Ironmateのnode-tests.ymlはlauncherのpath filterを持ち、tests/py_launch_wrappers.test.cjsがlock/digestとlauncher動作を検査する。MCP plannerの再テスト推薦はtest_github_ops_server.pyとtest_mcp_toolcall_lab_vendor_automation.py。後者に実lock登録検査がある。
- Ironmateのbrowser.jsonにある2026-11-08の6 pass / 0 failと、browser.gifのSHA-256一致を確認。現在headでのブラウザ再実行や目視品質の保証ではない。
- Aoiのledgerには過去のRejected判定が存在する。ledger読取のsuccess、命題の真偽、入力digestのnot_collectedを混同しない。
- 公開Dependencyから既存_adjacencyで再構成したファイル単位投影は、更新後373ノードをTopologicalSorterで順序化できた。subtree prefixesを空にした投影であり、元ビルド全体の完全検証ではない。初期グラフのリポジトリ相互参照29組は実行循環とは別。

## この変更で解消する事項

最新グラフはFLEET-47でMCPをinsufficient（registryとlockの差分unexpected 1）と報告した。カタログ登録とは別に、親vendor-consumers.jsonへplanner採用行を追加し、child-heads.jsonを確認済みの統合commit cd04472040f0db991c5138144c01f56a2b79e44bへ進める。他の子のbaselineは変更しない。

## 残る改善候補

1. 監査ごとに公開データを一度だけ取得し、全結果へsnapshot digest・取得時刻・repo revisionを付ける。途中更新で結果が混在しないことを確認する。
2. MCPの正常な0件をok=true / matched=falseにする。取得・解析エラーだけok=falseにする（MCP側の変更対象）。
3. query_reverifyでtruncated / unknown / candidatesを返す。Aoiの再確認200行が完全一覧に見えないようにする。
4. LICENSEをテスト欠落でなくlicense/byte再確認へ振り分ける。Ironmateの未登録6行とMCPの3行はすべて下流LICENSEだった。
5. 固定参照の完全性、最新版との差、更新先source削除を分離する。mainでsourceが削除されても既存pinが無効とは限らない。
6. why/reverifyでasserted_by、checkedAt、observedAt、digest、basis、compare/stateを保持し、repo名からファイル単位whyへ遷移できるようにする。
7. 実vendored plannerと保存snapshotの回帰テスト、および別の公開ネットワークsmokeを追加する。fixtureで実行循環と単なる相互参照を区別する。
8. JUnitの正常skip、0件選択、未取得、旧head成功を区別し、head SHAとrun URLを照会可能にする。未登録とはtested_by関係が未収録という意味であり、テスト不存在や失敗ではない。

今回は全lock行の網羅再検証・全推薦テストの実行・全Actionsの最新head照合は行っていない。グラフの取得成功をNode/Python等の規約準拠の証明として扱わない。
