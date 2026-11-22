# 横断開発ロードマップ

2026年11月22日に移管した7件を、元Issueの作成順に掲載する。同日、未完了だった18項目を各リポジトリの現行mainと照合して締めた。この文書に未完了の項目は残っていない。新しい作業は対象リポジトリのIssueで始める。

| 印 | 件数 | 意味 |
|---|---:|---|
| `[x]` | 6 | 確認済み。対象コミット・条件・結果を記録した |
| 🧊 | 8 | 見送り。何を問うていたかと、再開する条件を残した |
| 📌 | 4 | 対象リポジトリの文書へ戻した。以後はそこで追跡する |

各項目には番号ではなく、何を問うかを表す名前を付けた。旧Issueの番号（P1.4、G9など）は元Issueの本文で引ける。

11月22日のローカル確認は Linux x86_64、Node 26.13.1、Chrome for Testing 153.0.8010.12（headless）、各リポジトリのuv環境（Python 3.13.18、AoiとTalkCompressは3.14.6）で実行した。Playwright同梱のFirefox・WebKitは導入できない環境のため、ローカルの結果はChromiumに限る。

<a id="parent-56"></a>

## 親リポジトリの共通化

11月22日に親main `d2b0d62` で `python -m pytest -q tests/test_ci_status.py tests/test_vendor_stage.py` を実行。67件成功（サブテスト43件）。[対象コミット](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/d2b0d627d61ff339337a6974d846b683c170ff6a)

- [x] オンデマンドCI表示でrefをSHAに解決し、workflow・event・run・attempt・必須job・必須stepを照合する。実測と再現条件は下表を参照。対象コードは `ci_status.py` の `--verify-workflow`、回帰テストは `tests/test_expected_workflow.py`。
- [x] 旧Issueの優先4経路を下表で照合した。親のGit観測は一時ディレクトリで未初期化・空の作業ツリー・未追跡ファイル・出力打ち切りを実測した。変更を最後まで観測でき、変更が0件ならclean。未初期化は例外、打ち切りは `truncated=true, clean=false`。
- [x] 親の既存テストで書き込みの応答喪失・再試行・重複検知を検証した。`tests/test_gh_ops_api.py -k 'retry or lost_response or uncertain'` は24件成功。dispatch後のGETを検証する `tests/test_gh_workflow.py` は80件成功。
- [x] 元Issueで指定されたmarkdown・ascii_artistについて、親の登録・推奨と実採用を照合し、固定ソース、Git blob、SHA-256、配置バイト、LICENSEを確認した。各リポジトリで正当な追加、欠落、誤出典、重複、改変を含む回帰7件が成功。
- [x] **Portfolioのメタデータ。** gh_identityを統合コミットに固定し、配布物のLICENSE・stdout限定・欠落時のnull・detached HEADを検証した。CIの参考用JSONをダウンロードし、10キーと実際のcheckout SHA（PRテスト用merge commit `32fa7632`）を照合。ポータブルなツールのバージョンは数値表記に正規化した。[共通実装](https://github.com/myon-bioinformatics/gh_identity/pull/127)・[利用先](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/pull/130)・[Artifactのrun](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/actions/runs/45001841772)
- [x] **ブラウザの証跡。** PlaywrightとStagehand SDKが独立してページを撮影する。checkout `64e294e5` の両PNG（139,052 / 137,701バイト）をArtifactから読み戻し、run・SHA・バイト数・SHA-256と、日本語の一覧が表示されることを確認した。[実装](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/327)・[撮影run](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/45002235782)・[再現手順](browser-evidence.md)
- [x] **コメント要約の利用先。** アーカイブ済みFlutterは候補から外す。mcp-toolcall-labの `issue_comments_digest` で親 #56の16件から直近2件の元コメントIDとURLを取得した。[実装](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/dafcfac247b1b2f69e5f3f8ed49da61c7d3b5e97/src/mcp_toolcall_lab/github_ops_server.py#L218)・[測定記録](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56)
- [x] **コメント要約と解消根拠。** 親 #56の実コメント16件で元ID・URLを16件、指摘3件、直近の原文2件を確認。指摘番号と元IDを指定した解消報告は、その報告が対象headに明示的に結び付く場合に限って出典とともに記録する。引用、コード例、旧head、複数head、対象不明を回帰テストした。[使い方](gh-ops.md#source-linked-review-digest-issue-71)・[実コメント例](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56#issuecomment-6245469668)

gh_identityを追加採用する場合や、旧書き込み経路を削除する場合は、対象の利用元で同等性を検証する。共通Actionsの固定先を変更する場合も、そのheadのrun・artifactと互換性を確認する。採用の要否は利用元ごとに判断する。

11月22日に `python -S ci_status.py --verify-workflow` を実行し、GitHubの公開APIを読み取った。gh_identityはworkflow `377074728`、event `pull_request`、ref `feat/artifact-file-commit-rows`、SHA `4ca9ee8f76b091c10abf7e32b52a4b12d00e1004`。必須jobは `Vendor locked baseline`、stepは `Verify and recreate locked vendor snapshot`。

| 実行条件 | 観測 | 終了コード |
|---|---|---:|
| [初回・step 6](https://github.com/myon-bioinformatics/gh_identity/actions/runs/44906180384/job/136852159700#step:6) | run失敗。対象stepはスキップ | 1 |
| [再実行・step 6](https://github.com/myon-bioinformatics/gh_identity/actions/runs/44906180384/job/136865385160#step:6) | run・必須job・stepがすべて成功 | 0 |
| 同じ再実行に存在しないstepを指定 | 必須step欠落 | 2 |
| 同じ再実行にevent `push` を指定 | 実際のeventと不一致 | 2 |
| [親のキャンセル済みrun](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/43540609240)、push、attempt 1 | 元のSHA `9144124` でcancelled。観測時のmain `38ff0af` を指定するとSHA不一致 | 1 / 2 |

CI表示の実装は[親 #324](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/324)で統合した。以下は11月22日に確認したGit/GitHub操作の利用先。

| 利用元 | 確認できた経路・測定条件 |
|---|---|
| 親 | `git_inspector.status` は固定したgh_identityの `git_status` を呼ぶ。明示的な書き込みはgh_ops、Actionsの起動はgh_workflowが担当。[既存dispatch](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/44840371647)のGET応答を保存し、送信を模擬して再生した。元データは成功。run ID・workflow ID・event・SHA長を個別に変えた4件はすべて観測失敗で、模擬POSTは各1回。 |
| Ironmate | gh_ops・gh_identity・gh_workflowがlockにある。旧PR・コメントのラッパーは退役済み。固定したgh_opsのhelpと、隣接するgh_identityを使うgit pushのdry-runを実行し2件成功。[テスト](https://github.com/myon-bioinformatics/Ironmate/blob/9e6b5ed67c807213ee8847ba3294af2fc82929f6/tests/test_tooling.py#L578) |
| BrowserTestKit | 現行lockにgh_ops・gh_identity・git_inspectorの配布行は0件。旧ブラウザ用コピーの移行は過去の記録として扱う。[対象lock](https://github.com/myon-bioinformatics/BrowserTestKit/blob/34f8f7ad2f1b43251688ec983b8e64abad634843/vendor.lock.json) |
| mcp-toolcall-lab | 固定したgh_opsを使うMCPサーバーを起動し、FastMCPクライアントの `tools/list` で15件を取得。PR状態、コメント要約、CI、URL等の読み取りを公開し、書き込みツールの登録は0件。[アダプター](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/dafcfac247b1b2f69e5f3f8ed49da61c7d3b5e97/src/mcp_toolcall_lab/github_ops_server.py#L141) |

親の `vendor_lock_reconcile.py --consumer ... --lock ...` に各mainのlockを渡して再集計した。2件とも登録の欠落・余分な登録は0件、同一ソースで推奨と採用の固定先が異なるものは各1件。固定先のソースと配置ファイルのバイト照合は別途実行した。

| 検証したmain | 固定ソースと配置の一致 | 実装PRのCIに帰属したArtifact |
|---|---:|---|
| [markdown `2ce6832`](https://github.com/myon-bioinformatics/markdown/commit/2ce6832dbe49895c8f06b6fc725579ca16ff8f53) | 8/8 | [run 44962733429](https://github.com/myon-bioinformatics/markdown/actions/runs/44962733429) の18件。全件でrun IDとPR head `a2f0c5a` が一致 |
| [ascii_artist `4234488`](https://github.com/myon-bioinformatics/ascii_artist/commit/4234488306901d8ead9fc9a098f610e049374800) | 7/7 | [run 44997723139](https://github.com/myon-bioinformatics/ascii_artist/actions/runs/44997723139) の17件。全件でrun IDとPR head `4f15df3` が一致 |

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56)

<a id="pages-32"></a>

## 公開リポジトリのPages展開

11月21日の再集計では、公開・非アーカイブ29件中19件でPagesが有効。ONTOの公開と幅320・390・1440 pxでの確認を含む。[再集計と公開検証](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/issues/32#issuecomment-7060093628)

- [x] `pages-recount`（公開状況は今いくつか）。11月22日にGitHub公開APIの `has_pages` を読み、公開・非アーカイブ29件中19件で有効。11月21日の値と一致した。無効の10件はBlueProbe、BrowserTestKit、Err-o-RoadRunner、PythDRagoras、QueRyu、SakAnalytics、search_seq_including_spaces、TalkCompress、TaxOntOy、TopOntoLogy。スマートフォンでの代表操作は、入力を持つHTML取込について次の項目で確認した。実機での確認と他サイトの個別操作は見送る。再開条件：入力を持つツールを新しく公開するとき、そのブラウザテストにモバイルの組を足す。
- [x] `html-import-paste-file`（貼り付けとファイル選択で同じ結果を取り出せるか）。portfolio main `e940a5c` で `tests/html-import.spec.js` を実行し10件成功。デスクトップと390×844・タッチのエミュレーションの両方で、貼り付け、ファイル選択、条件変更、結果JSONの保存、同一入力の再取得の一致を確認した。共有fixtureは19ケース。`tests/test_site.py` は49件成功・1件スキップ。
- 🧊 `moved-link-check`（移管後も旧リンク・query・hashから到達できるか、変換系ツールをどう扱うか）。移管の予定がなく対象が決まらない。再開条件：ツールを別リポジトリや別パスへ移すとき、そのPRで旧URLからの到達を確かめる。入力の保存を利用者の選択とし、公開用の合成fixtureと分ける方針は維持する。
- 🧊 `on-device-llm`（端末内LLM・Decision Modelsで、再利用する回答・端末内データ・実行環境を明示できるか）。portfolio mainに該当する実装がない。再開条件：端末内で推論するツールを公開すると決めたとき。

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/issues/32)

<a id="lab-122"></a>

## MCPの履歴圧縮・推論評価

旧Issueには固定GGUFの圧縮前後の比較と品質回帰、モデルを起動しないアダプターの87件のテスト結果がある。

- [x] `compaction-declines-safely`（過去の制約を保ち、危ない圧縮は辞退するか）。lab main `dafcfac` で `pytest tests/test_compaction.py` 93件成功。削除範囲に過去のユーザー指示があると圧縮を辞退すること、圧縮前後でツール名と引数が一致すること（決定的なCPU代替モデル）を含む。UIからproxy・実モデルまでの経路は確認対象から外した。`docs/compaction-safety.md` が配備済みproxyではないと明記しており、経路自体がない。再開条件：圧縮をproxyへ組み込むと決めたとき、実モデルとMCPのツール選択で測る。
- 🧊 `gguf-cpu-metrics`（CPUのGGUFでcold/warm時間・ピークRSS・品質と予算を記録できるか）。ベンチのコードと再現手順は `docs/compaction-gguf-benchmark.md` に残る。mainにこれを実行するワークフローと保存済みの計測結果はなく、今回の環境はDockerを起動できないため再実行していない。再開条件：上と同じ。再開時は同文書の「Actions workflow」の記述を現状に合わせる。
- 🧊 `cache-kinds-ttl`（完全一致キャッシュ・近似応答・再計算・エンジン固有KVを区別し、TTL・失効・上限を検証できるか。GPU/NPUの評価を含む）。今回は照合していない。再開条件：上と同じ。GPU/NPUは機器と比較基準を用意できたとき。
- 🧊 `littlebit-tool-eval`（LittleBitの候補生成と独立評価を分け、seed・bit数・tool名と引数・架空tool率を記録できるか）。lab mainにLittleBitのコードはなく、文書1件の言及だけがある。同じ行にあったブラウザ操作とUI回帰の組合せ、親のvendor登録、JUnitのhead帰属、READMEと実装の対応は、それぞれ既存の検査の担当で、今回は個別に照合していない。再開条件：labにLittleBitの候補生成を入れるとき。

[元Issueと受入条件](https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/122)

<a id="aoi-30"></a>

## Aoiの研究記録と子リポジトリ

BlueProbe同期とJUnit固定先更新はmainに統合済み。#6・#42のheadもmainの履歴に含まれる。[BlueProbe同期](https://github.com/myon-bioinformatics/Aoi/pull/27)・[JUnit更新](https://github.com/myon-bioinformatics/Aoi/pull/29)

- [x] `unread-input-kept`（読めなかった入力を原文と範囲つきで残し、既知回答やキャッシュで確定回答にしないか）と `registration-status`（事前・事後・不明を区別するか）。Aoi main `5f56353` で `pytest queryu/tests/test_queryu_ask.py pythdragoras/tests/test_registration_audit.py nagoyaction/tests/test_inbox.py -k "q5 or unread or unknown_conditions or cache or registration or regist or posthoc or prereg"` を実行し60件成功。Q5は原文と `unread_spans` のstart 7・end 20を保ち、直接・既知回答・キャッシュ・両方の4経路で判定不能のまま。受付の受領記録にも同じ原文と範囲が残る。E25を不明のまま置く理由は `docs/registration-metadata-audit.md` にある。
- 📌 `r001-followups`（R001のF1〜F7。訂正の次版への反映、`p=1` の端点処理、原資料との照合、段階別遷移の取り込み、追加研究、2026年への対象拡張、公開と差分の機能）。Aoiの `research/gpt-r001-score-allocation/PR3_FOLLOWUP.md` に7件のチェックリストがあり、そこで追跡する。LittleBitの独立確認に未閲覧のデータを使う条件は、Aoiの研究記録で着手時に定める。
- 🧊 `child-checks-adoption`（Aoiの `scripts/child_checks.py` を子へ配るか）と `exit-code-constants`（終了コード64・65・66、ε・ゼロ割りを共通化するか）。`shared-tools.lock.json` で `child_checks.py` の利用元は0件。複数リポジトリで同じ検査は親の `fleet_check.py` をvendorで配る形になったため、Aoiからは配らない。64・65・66は11月21日の再確認でsysexits.hの意味のリテラルに揃っており、定数は増やさない。再開条件：呼び出し側で値や意味の食い違いが見つかったとき。

[元Issueと受入条件](https://github.com/myon-bioinformatics/Aoi/issues/30)

<a id="btk-117"></a>

## BrowserTestKitの実機差と操作

Windows・macOSのChromiumでloopbackの通信断と復旧を検証済み。11月22日にPython経路のURL証跡テスト10件が成功。[OS別の実行結果](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44915995188)

- 📌 `real-os-matrix`（OS・エンジン・channelごとの差を、実機とエミュレーションを分けて再現できるか。HTTPエラーと接続失敗、権限の付与と解除、ファイルの送受信を含む）。Linuxの3エンジンは main `f974ca0` のCIで成功（[run 44999087770](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44999087770)）。WindowsとmacOSはChromiumの通信断と復旧まで。残りはBrowserTestKitの `docs/page-inspect-platform-scenarios.md` の「Closing record for issue #117」で追跡する（[BrowserTestKit #185](https://github.com/myon-bioinformatics/BrowserTestKit/pull/185)）。
- [x] `url-value-leak`（初回とgoto後の保存JSONにqueryとfragmentの値が残らないか）。main `f974ca0` で16件成功。PythonとNode 26.13.1の両入口で、証跡用URL、保存JSON、compare、エラー出力を確認した。FirefoxとWebKitはローカルでは未実行。
- 📌 `frame-shadow-address`（子フレームとopen shadow rootを別の番地として扱えるか）ほか。フレームとshadow rootは同じ環境で9件成功。Android、headed、Stagehand、transportは見送りとし、再開条件を同じ文書に記録した（#185）。

[元Issueと受入条件](https://github.com/myon-bioinformatics/BrowserTestKit/issues/117)

<a id="cli-29"></a>

## 横断CLIの引数・出力とテスト

P0、P1.1〜P1.3、P1.5のlinkcheck、P1.6のmarkdownスクリプトは統合済み。cli_argsには選択用glob、名前付きパス、範囲付き整数が入った。[markdown #158](https://github.com/myon-bioinformatics/markdown/pull/158)・[cli_args #59](https://github.com/myon-bioinformatics/cli_args/pull/59)

- [x] `help-exit-sweep`（`--help` と不明な引数で、出力先と終了コードが揃っているか）。NagoyAction `fff1cde`、BrowserTestKit `f974ca0`、PySaiyanWeBoo（旧web-ui）`9f5a730`、gh_identity `3111c17` の入口とサブコマンド計127件を実行した。126件で `--help` は終了0・標準出力、不明な引数は終了2・標準エラー。例外は `btk.py check-png` で、存在しないファイルにtracebackを出していた（[BrowserTestKit #185](https://github.com/myon-bioinformatics/BrowserTestKit/pull/185) で修正）。旧Issueが名指しした `twin_parity.py`、`collection-cli.cjs`、`tool/flow_diagram.py`、`web_ui.py` は現行mainにない。ヘルプ文のない引数は概算でgh_identity 260（88サブコマンド中74）、btk.py 53（18中11）、nagoyaction.py 8（9中2）。補完は各サブコマンドを次に編集するときに行う。
- 🧊 `one-test-command`（リポジトリごとに1コマンドでテストを実行できるか）、`format-json-flag`（`--json` と `--format json` を揃えるか）、`neighbor-cli-help`（QueRyu・PythDRagoras・SakAnalyticsなど周辺ツールにhelpとJSON出力を足すか）。一括で揃える対象が大きいため、実例に基づく個別対応に切り替える。再開条件：エージェントやCIが呼び方の違いで失敗した実例が出たとき、そのCLIから直す。従来のHTML出力とパイプ利用の互換は、直すときのテストに含める。

[元Issueと受入条件](https://github.com/myon-bioinformatics/cli_args/issues/29)

<a id="compression-40"></a>

## TalkCompressの圧縮と往復評価

G2/G3は実装済み。G4の既存採用判定と、新規モデル単独での成績を区別して記録する。[G2/G3の実装](https://github.com/myon-bioinformatics/TalkCompress/pull/43)

- [x] G4は現在の話者1億人未満の採用18形（cs 8・nl 8・pl 2）をQwen2.5-1.5Bで新規測定。各形3テンプレート×3 seedで162/162試行完了。復元はcs 3/72、nl 3/72、pl 0/18。正答率0.9に達した形は0/18。現行nlの英語現在完了の必須形は0件。採用判定用fixtureは従来のまま。この測定は当該モデルと対象言語に限る。[入力・回答・採点・再現手順](https://github.com/myon-bioinformatics/TalkCompress/pull/192)
- 📌 `compress-candidates-g1-g8`（箇条書き化、固定スロットの履歴要約、保持要素の検査、質問応答での忠実度、構造化データ、tokenizer別の計測、キャッシュを保つ要約、読み手別の形式を、同じ入力で比べられるか）。TalkCompressのREADME「圧縮候補 G1〜G14 の現在地」で候補ごとに追跡する（[TalkCompress #194](https://github.com/myon-bioinformatics/TalkCompress/pull/194)）。
- 🧊 `context-api-comparison`（古いtool結果の消去、サーバー側要約、焦点指定、必須集合の再注入を比べ、入力tokenの実測と推定、キャッシュの書き込み・読み出し費用を対応付けられるか。G9〜G14）。外部APIやClaude Codeの挙動を課金つきで測る必要がある。再開条件：実測に使う利用枠と比較用の会話ログが揃ったとき。同じREADMEの表に記録した（#194）。

[元Issueと受入条件](https://github.com/myon-bioinformatics/TalkCompress/issues/40)
