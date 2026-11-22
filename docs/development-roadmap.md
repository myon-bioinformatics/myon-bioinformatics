# 横断開発ロードマップ

2026年11月22日に移管した7件を、元Issueの作成順に掲載する。完了時は対象コミット、検証条件、結果を該当欄に記録する。旧Issueの実装案や対象数は、着手時点の利用先・採用データと照合する。前提が変わった場合は、元の記録へのリンクと、今も確認する必要がある振る舞いを残す。

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

- [ ] 対象と公開状況を再集計する。各サイトの入力・結果・実行場所を記録し、スマートフォンだけで代表操作を完了できることを確認する。
- [ ] 最初のHTML抽出では貼り付けとファイル選択を試す。本文・見出し・リンクの期待値を共通fixtureで検証し、同一入力での再抽出、結果の保存を確認する。
- [ ] 移管先の公開後に旧リンク・query・hashを検証する。変換系は現役の利用先と端末内での実行可否を確認して個別に判断する。入力の保存は利用者の選択とし、公開用の合成fixtureと分ける。
- [ ] 端末内LLM・Decision Modelsの検証では、再利用する公開回答、端末内データ、検証済みの入出力と実行環境を明示する。

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/issues/32)

<a id="lab-122"></a>

## MCPの履歴圧縮・推論評価

旧Issueには固定GGUFの圧縮前後の比較と品質回帰、モデルを起動しないアダプターの87件のテスト結果がある。

- [ ] UIからproxy、モデルまでのopt-in、過去の制約の保持、圧縮辞退を実際のmulti-turn入力とMCPのツール選択で確認する。
- [ ] CPUで明示導入したGGUFをCLIと既存利用元から呼ぶ。モデル・tokenizer・実行系の版、入力ハッシュ、実機、cold/warm時間、ピークRSS、品質と予算を記録する。
- [ ] 完全一致キャッシュ、近似応答、再計算、エンジン固有KVを識別し、TTL・失効・上限を検証する。GPU/NPUは機器と比較基準を用意できた場合に評価する。
- [ ] LittleBitの候補生成と独立評価を分け、seed、bit数、tool名・引数、架空tool率を記録する。ブラウザ操作・UI回帰の組合せ、親のvendor登録、JUnitのhead帰属、READMEと実装の対応を利用元ごとに確かめる。

[元Issueと受入条件](https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/122)

<a id="aoi-30"></a>

## Aoiの研究記録と子リポジトリ

BlueProbe同期とJUnit固定先更新はmainに統合済み。#6・#42のheadもmainの履歴に含まれる。[BlueProbe同期](https://github.com/myon-bioinformatics/Aoi/pull/27)・[JUnit更新](https://github.com/myon-bioinformatics/Aoi/pull/29)

- [ ] 登録時期（事前・事後・不明）、理由、証拠、独立確認を区別し、E/Pの記録、JSONL、表示、キャッシュの整合を検証する。未解釈入力はQ5の原文と範囲 `[7,20)` を保ち、既知回答・キャッシュで確定回答にならないことを現行版で確かめる。
- [ ] R001のF1〜F7は項目ごとに原資料・端点・段階遷移を照合する。LittleBitの独立確認には未閲覧のデータを使い、探索済みバックテストと分ける。
- [ ] 子の共通検査を採用する際、値・例外・終了コード・テスト結果を比較し、実際の利用元を `shared-tools.lock.json` に記録する。64・65・66、ε・ゼロ割り、記録処理の共通化は呼び出し側の実例に基づいて判断する。

[元Issueと受入条件](https://github.com/myon-bioinformatics/Aoi/issues/30)

<a id="btk-117"></a>

## BrowserTestKitの実機差と操作

Windows・macOSのChromiumでloopbackの通信断と復旧を検証済み。11月22日にPython経路のURL証跡テスト10件が成功。ローカルの実ブラウザ再検証はソケット制限、Node経路は要求バージョン未達。[OS別の実行結果](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44915995188)

- [ ] OS・エンジン・channel・viewport・locale等を記録し、実機とエミュレーションを区別して差分を再現する。対応範囲ごとにHTTPエラーと接続失敗、権限の付与・解除、ファイル送受信・取消を検証する。
- [ ] 初回・goto後の保存JSONからquery・fragment値が漏れないことを、対応するブラウザとNode 26以降の入口で再確認する。
- [ ] 子フレームとopen shadow rootを区別し、再描画後はlocatorを再取得する。移動後のセッション再観測、headed、任意のStagehandとAndroidは実行範囲を明記する。操作CLIの契約安定後にtransportの要否を判断する。

[元Issueと受入条件](https://github.com/myon-bioinformatics/BrowserTestKit/issues/117)

<a id="cli-29"></a>

## 横断CLIの引数・出力とテスト

P0、P1.1〜P1.3、P1.5のlinkcheck、P1.6のmarkdownスクリプトは統合済み。cli_argsには選択用glob、名前付きパス、範囲付き整数が入った。[markdown #158](https://github.com/myon-bioinformatics/markdown/pull/158)・[cli_args #59](https://github.com/myon-bioinformatics/cli_args/pull/59)

- [ ] P1.4〜P1.10の残りは現行CLIの `--help`、標準出力・標準エラー、終了コードを確認して進める。対象にはNagoyActionの残り、BrowserTestKitのterminal browser、web-ui、gh_identityがある。DRAgoWingのJSON出力とascii_artistの製品CLIは既に存在する。
- [ ] P2〜P4は利用元ごとにルートから実行できるテストコマンド、JSON指定と既定出力、無効入力・部分成功を検証する。従来のHTML出力やパイプ利用の互換性をテストに含める。

[元Issueと受入条件](https://github.com/myon-bioinformatics/cli_args/issues/29)

<a id="compression-40"></a>

## TalkCompressの圧縮と往復評価

G2/G3は実装済み。G4の既存採用判定と、新規モデル単独での成績を区別して記録する。[G2/G3の実装](https://github.com/myon-bioinformatics/TalkCompress/pull/43)

- [x] G4は現在の話者1億人未満の採用18形（cs 8・nl 8・pl 2）をQwen2.5-1.5Bで新規測定。各形3テンプレート×3 seedで162/162試行完了。復元はcs 3/72、nl 3/72、pl 0/18。正答率0.9に達した形は0/18。現行nlの英語現在完了の必須形は0件。採用判定用fixtureは従来のまま。この測定は当該モデルと対象言語に限る。[入力・回答・採点・再現手順](https://github.com/myon-bioinformatics/TalkCompress/pull/192)
- [ ] G1〜G8は箇条書き・固定スロット履歴・保持要素、構造化データ、tokenizer、キャッシュ、読み手別形式を同じ入力で評価する。識別子・URL・数値・否定・条件の保持と秘密情報の除去を確認する。
- [ ] G9〜G14は古いtool結果の消去、サーバー側要約、焦点指定、必須集合の再注入を比較する。入力tokenの実測と推定、キャッシュ書き込み・読み出し費用、G4の品質を対応付ける。

[元Issueと受入条件](https://github.com/myon-bioinformatics/TalkCompress/issues/40)
