# 横断開発ロードマップ

2026年11月22日に移管した7件を、元Issueの作成順に掲載する。同日、現行mainとの差分を照合し、旧案を無理に通すのではなく、いまの方針の中でベストプラクティス・ベストエフォートを採用してクローズした。

分類は次のとおり。完了時は対象コミット、検証条件、結果を該当欄に記録する。旧Issueの実装案や対象数は、着手時点の利用先・採用データと照合する。前提が変わった場合は、元の記録へのリンクと、今も確認する必要がある振る舞いを残す。

- **A** 現行mainで実質済み。証拠（PR・コミット・ファイル・公開URL）を[完了](#完了)に残す。
- **B** 部分済み、または現行方針で小さく閉じてよい。残件にするか、完了の注記とする。
- **C** 現行方針と矛盾するか、前提が変わった。[見送った項目](#見送った項目)に理由を1行で残す。
- **D** 今も残す価値がある。[残件](#残件)に残す。

リポジトリ名は現行の公開名を使う（`web-ui` は [PySaiyanWeBoo](https://github.com/myon-bioinformatics/PySaiyanWeBoo)、`browser-test-kit` は [BrowserTestKit](https://github.com/myon-bioinformatics/BrowserTestKit)）。

「ローカル確認」と書いた結果は、11月22日に Linux x86_64、Node 26.13.1、Chrome for Testing 153.0.8010.12（headless）、各リポジトリのuv環境で実行した。Playwright同梱のFirefox・WebKitは導入できない環境のため、結果はChromiumに限る。

<a id="completed"></a>

## 完了

<a id="parent-56"></a>

### 親リポジトリの共通化

11月22日に親main `d2b0d62` で `python -m pytest -q tests/test_ci_status.py tests/test_vendor_stage.py` を実行。67件成功（サブテスト43件）。[対象コミット](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/d2b0d627d61ff339337a6974d846b683c170ff6a)

- オンデマンドCI表示でrefをSHAに解決し、workflow・event・run・attempt・必須job・必須stepを照合する。対象コードは `ci_status.py` の `--verify-workflow`、回帰テストは `tests/test_expected_workflow.py`。[親 #324](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/324)
- 親のGit観測は一時ディレクトリで未初期化・空の作業ツリー・未追跡ファイル・出力打ち切りを実測した。変更を最後まで観測でき、変更が0件ならclean。未初期化は例外、打ち切りは `truncated=true, clean=false`。
- 親の既存テストで書き込みの応答喪失・再試行・重複検知を検証した。`tests/test_gh_ops_api.py -k 'retry or lost_response or uncertain'` は24件成功。dispatch後のGETを検証する `tests/test_gh_workflow.py` は80件成功。
- markdown・ascii_artistについて、親の登録・推奨と実採用を照合し、固定ソース、Git blob、SHA-256、配置バイト、LICENSEを確認した。各リポジトリで正当な追加、欠落、誤出典、重複、改変を含む回帰7件が成功。
- **Portfolioのメタデータ。** gh_identityを統合コミットに固定し、配布物のLICENSE・stdout限定・欠落時のnull・detached HEADを検証した。[共通実装](https://github.com/myon-bioinformatics/gh_identity/pull/127)・[利用先](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/pull/130)・[Artifactのrun](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/actions/runs/45001841772)
- **ブラウザの証跡。** PlaywrightとStagehand SDKが独立してページを撮影する。[実装](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/327)・[撮影run](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/45002235782)・[再現手順](browser-evidence.md)
- **コメント要約の利用先。** アーカイブ済みFlutterは候補から外す。mcp-toolcall-labの `issue_comments_digest` で親 #56の16件から直近2件の元コメントIDとURLを取得した。[実装](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/dafcfac247b1b2f69e5f3f8ed49da61c7d3b5e97/src/mcp_toolcall_lab/github_ops_server.py#L218)
- **コメント要約と解消根拠。** 親 #56の実コメント16件で元ID・URLを16件、指摘3件、直近の原文2件を確認。[使い方](gh-ops.md#source-linked-review-digest-issue-71)・[実コメント例](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56#issuecomment-6245469668)

11月22日に `python -S ci_status.py --verify-workflow` を実行し、GitHubの公開APIを読み取った。gh_identityはworkflow `377074728`、event `pull_request`、ref `feat/artifact-file-commit-rows`、SHA `4ca9ee8f76b091c10abf7e32b52a4b12d00e1004`。必須jobは `Vendor locked baseline`、stepは `Verify and recreate locked vendor snapshot`。

| 実行条件 | 観測 | 終了コード |
|---|---|---:|
| [初回・step 6](https://github.com/myon-bioinformatics/gh_identity/actions/runs/44906180384/job/136852159700#step:6) | run失敗。対象stepはスキップ | 1 |
| [再実行・step 6](https://github.com/myon-bioinformatics/gh_identity/actions/runs/44906180384/job/136865385160#step:6) | run・必須job・stepがすべて成功 | 0 |
| 同じ再実行に存在しないstepを指定 | 必須step欠落 | 2 |
| 同じ再実行にevent `push` を指定 | 実際のeventと不一致 | 2 |
| [親のキャンセル済みrun](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/43540609240)、push、attempt 1 | 元のSHA `9144124` でcancelled。観測時のmain `38ff0af` を指定するとSHA不一致 | 1 / 2 |

採用の要否は利用元ごとに判断する。共通Actionsの固定先を変更する場合は、そのheadのrun・artifactと互換性を確認する。BrowserTestKitの現行lockにgh_ops・gh_identityは入っておらず、書き込み経路が必要になるまで追加採用はしない。

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56)

<a id="pages-32-done"></a>

### 公開リポジトリのPages（済み分）

2026年11月22日の再集計では、公開・非アーカイブ29件中19件でPagesが有効（11月21日時点の18件から、[ONTOの紹介ページ](https://myon-bioinformatics.github.io/ONTO/)を追加）。同日中に残り10件を展開し、29件すべてで有効になった。

残り10件の展開（11月22日）: 利用ガイド（`docs/guide.page.json`）と、cli_args などと同じ `pages.yml` を入れた（[BlueProbe #62](https://github.com/myon-bioinformatics/BlueProbe/pull/62)、[BrowserTestKit #187](https://github.com/myon-bioinformatics/BrowserTestKit/pull/187)、[Err-o-RoadRunner #76](https://github.com/myon-bioinformatics/Err-o-RoadRunner/pull/76)、[PythDRagoras #56](https://github.com/myon-bioinformatics/PythDRagoras/pull/56)、[QueRyu #51](https://github.com/myon-bioinformatics/QueRyu/pull/51)、[SakAnalytics #54](https://github.com/myon-bioinformatics/SakAnalytics/pull/54)、[search_seq_including_spaces #87](https://github.com/myon-bioinformatics/search_seq_including_spaces/pull/87)、[TalkCompress #196](https://github.com/myon-bioinformatics/TalkCompress/pull/196)、[TaxOntOy #15](https://github.com/myon-bioinformatics/TaxOntOy/pull/15)、[TopOntoLogy #18](https://github.com/myon-bioinformatics/TopOntoLogy/pull/18)）。10件とも main の配備ジョブとデプロイメントの状態が success。公開先は `https://myon-bioinformatics.github.io/<リポジトリ名>/`。ページを開いての表示確認は、公開前のビルドを 390 px 幅と 1200 px 幅の Chromium で描画した2件（SakAnalytics、BrowserTestKit）まで。10件とも紹介のページで入力を持たない。

最初のHTML抽出（貼り付けとファイル選択、本文・見出し・リンク、同一入力での再抽出、結果の保存）は公開済み。[HTML文書取込](https://myon-bioinformatics.github.io/tools/html-import/)（HTTP 200）。契約とdesktop/mobileの検証は [docs/html-import.md](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/main/docs/html-import.md)。

ローカル確認: portfolio main `e940a5c` で `tests/html-import.spec.js` 10件成功。デスクトップと390×844・タッチのエミュレーションの両方で、貼り付け、ファイル選択、条件変更、結果JSONの保存、同一入力の再取得の一致を確かめた。`tests/test_site.py` は49件成功・1件スキップ。19/29の集計はGitHub公開APIの `has_pages` でも一致した。

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/issues/32)

<a id="lab-122"></a>

### MCPの履歴圧縮・推論評価（済み分）

- モデルを起動しないアダプター試験87件、保守的な履歴圧縮core（#116）、履歴計測（#118）、固定GGUFでの圧縮前後比較基盤（#119）は実装済み。
- CPUでの実GGUF測定は [compaction-gguf-benchmark.md](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/main/docs/compaction-gguf-benchmark.md) に記録した（SmolLM2-135M、llama.cpp imageをSHA固定）。
- ローカル確認: lab main `dafcfac` で `pytest tests/test_compaction.py` 93件成功。削除範囲に過去のユーザー指示があると圧縮を辞退すること、圧縮前後でツール名と引数が一致すること（決定的なCPU代替モデル）を含む。
- GGUFベンチを実行するワークフローは2026-11-06に削除済み（`4088c0d`）で、文書だけが「Actions workflow」を案内していた。文書をローカルのDocker実行に合わせた（[mcp-toolcall-lab #223](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/223)）。
- vendor追従: [PR #120](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/120)（2026-10-14マージ。親由来のgh_opsへ再同期しgit_inspectorを削除）と [PR #121](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/121)（共有JUnit collector pin）は統合済み。現行 `vendor.lock.json` に親の `gh_ops.py` と `gh_identity.py`、PySaiyanWeBooが入る。[PR #221](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/221) で文書の固定先記載を現状に合わせた。
- xprobeへのLittleBit入力生成の採用判断は完了。

[元Issueと受入条件](https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/122)

<a id="aoi-30-done"></a>

### Aoiの研究記録（済み分）

BlueProbe同期とJUnit固定先更新はmainに統合済み。[BlueProbe同期](https://github.com/myon-bioinformatics/Aoi/pull/27)・[JUnit更新](https://github.com/myon-bioinformatics/Aoi/pull/29)

未解釈入力はmainの契約として定着している。Q5「P121 は 2026 年で確かめられた？」は `unread_spans=[{start:7,end:20,raw_text:"2026 年で確かめられた"}]` を保ち、`parse_status=undetermined` のまま既知回答・キャッシュで確定回答にしない。[queryu/ask/ask.py](https://github.com/myon-bioinformatics/Aoi/blob/main/queryu/ask/ask.py)・[test_queryu_ask.py](https://github.com/myon-bioinformatics/Aoi/blob/main/queryu/tests/test_queryu_ask.py)

ローカル確認: Aoi main `5f56353` で `pytest queryu/tests/test_queryu_ask.py pythdragoras/tests/test_registration_audit.py nagoyaction/tests/test_inbox.py -k "q5 or unread or unknown_conditions or cache or registration or regist or posthoc or prereg"` 60件成功。Q5は直接・既知回答・キャッシュ・両方の4経路で判定不能のまま、受付の受領記録にも同じ原文と範囲が残る。

LittleBitのc001候補生成・比較と年別バックテストはmain導入済み。追加識別力は当該設定では未観測。未閲覧データでの独立確認はデータ計画がないため[見送った項目](#見送った項目)へ移した。

子のshrink/py_launchはNagoyAction・QueRyu・DRAgoWing等に採用済み。[shared-tools.lock.json](https://github.com/myon-bioinformatics/Aoi/blob/main/shared-tools.lock.json)

R001のF1〜F7は、11月22日に main `5f56353` の受付・PLAN/RESULTS・コードと照合して項目ごとに判定した（[Aoi #235](https://github.com/myon-bioinformatics/Aoi/pull/235)）。完了にできる項目は0件。F1・F2は提供元の訂正版待ち、F3は原資料の取得が前提、F5は計画が未固定、F6は条件を維持中、F7はDRAgoWingの機能追加待ち。F4は引き継ぎパッケージ `aoi_pr3_stage_transitions.zip` が残っておらず、見送った（[Aoi #236](https://github.com/myon-bioinformatics/Aoi/pull/236)）。F1〜F7そのものは Aoi の `research/gpt-r001-score-allocation/PR3_FOLLOWUP.md` で追跡を続ける。

[元Issueと受入条件](https://github.com/myon-bioinformatics/Aoi/issues/30)

<a id="btk-117-done"></a>

### BrowserTestKitの実機差と操作（済み分）

- 環境項目の記録と比較: [PR #182](https://github.com/myon-bioinformatics/BrowserTestKit/pull/182)
- Windows・macOS Chromiumでのloopback通信断と復旧: [PR #183](https://github.com/myon-bioinformatics/BrowserTestKit/pull/183)・[OS別の実行結果](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44915995188)
- HTTPエラー・遅延、権限の付与・解除、ファイル送受信の限定fixture: `btk.py scenario`。[契約](https://github.com/myon-bioinformatics/BrowserTestKit/blob/main/docs/rea-observation.md) Linuxの3エンジンは main `f974ca0` のCIで成功（[run 44999087770](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/44999087770)）。
- 保存するURL証跡からquery・fragmentを除く方針はPythonとNodeの入口で共有。ローカル確認: main `f974ca0` で16件成功。PythonとNode 26.13.1の両入口で、初回・goto後の保存JSON、compare、エラー出力に値が残らない。FirefoxとWebKitは未実行。

[元Issueと受入条件](https://github.com/myon-bioinformatics/BrowserTestKit/issues/117)

<a id="cli-29-done"></a>

### 横断CLIの引数・出力とテスト（済み分）

P0（共有Node launcherのNode 24更新）、P1.1 NVD、P1.2 yourself、P1.3 xprobe、P1.5 NagoyAction linkcheck、P1.6 markdownスクリプトは統合済み。ascii_artistの製品CLI（`python ascii_artist.py COMMAND ...`）もある。

共有契約はcli_argsに置く。`--format` を正とし、`--json` は `--format json` の糖衣。[cli_args #59](https://github.com/myon-bioinformatics/cli_args/pull/59)・[markdown #158](https://github.com/myon-bioinformatics/markdown/pull/158)・[調査記録（2026-11-07）](https://github.com/myon-bioinformatics/cli_args/blob/main/docs/cli-argument-patterns.md)

調査の再実行（11月22日、[cli_args #61](https://github.com/myon-bioinformatics/cli_args/pull/61)）: 公開・非アーカイブ29件のクローンに `scripts/survey_cli_surfaces.py` を実行し、調査表を更新した。パーサーを作る `.py` は376件で、29件すべてに1つ以上ある。`--format` を持つファイルは27、choicesがちょうど `text|json` のものは3。調査が `ArgumentParser` のサブクラスと `cli_args.make_parser` を数えておらず、共有契約を採用したCLIなど7件を見落としていたので、同じPRで直した。

[元Issueと受入条件](https://github.com/myon-bioinformatics/cli_args/issues/29)

<a id="compression-40-done"></a>

### TalkCompressの圧縮と往復評価（済み分）

G2/G3は実装済み（`shared/hist_state.py`、`shared/keep_check.py`）。[G2/G3の実装](https://github.com/myon-bioinformatics/TalkCompress/pull/43)

G4は現在の話者1億人未満の採用18形（cs 8・nl 8・pl 2）をQwen2.5-1.5Bで新規測定した。各形3テンプレート×3 seedで162/162試行完了。復元はcs 3/72、nl 3/72、pl 0/18。正答率0.9に達した形は0/18。採用判定用fixtureは従来のまま。この測定は当該モデルと対象言語に限る。[入力・回答・採点・再現手順](https://github.com/myon-bioinformatics/TalkCompress/pull/192)・[ライブ測定](https://github.com/myon-bioinformatics/TalkCompress/blob/main/docs/measurements/g4-live.md)

G6のtokenizer調査（`scripts/tokenizer_compat.py`、`docs/o200k-lookup/`）と、G8の読み手向け確認（`scripts/ai_readability.py`）は部分的に存在する。 G1〜G14の候補ごとの現在地は、TalkCompressのREADMEに表で置く（[TalkCompress #194](https://github.com/myon-bioinformatics/TalkCompress/pull/194)）。

[元Issueと受入条件](https://github.com/myon-bioinformatics/TalkCompress/issues/40)

<a id="dropped"></a>

## 見送った項目

- 端末内LLM・Decision ModelsをPagesの完了条件にすること。有料の外部推論APIを前提にしない現行方針と合わず、公開面の完了判定からは外す。
- 変換系GUIのブラウザ化。対象リポジトリはアーカイブ済みで、紹介だけのデスクトップ変換アプリを延命しない。
- Flutter（`flutter_navigation_basic`）へのmetadata/CI移行と、LittleBitのFlutter座標適用。対象はアーカイブ済み。復活する場合はDart/Flutterの挙動変更として別途承認が必要。
- Aoiの `child_checks.py` を子へ強制採用すること。現行 `consumers` は空だが、shrink/py_launchは既に採用がある。採用先がないことを欠陥にしない。
- 終了コード64・65・66の共通化。`child_checks.py` は0/1/2を使う。 Aoi の各サブツリーの中でリテラルを `EX_*` 定数に置き換える変更（[Aoi #234](https://github.com/myon-bioinformatics/Aoi/pull/234)）は別件で、子をまたぐ共通化ではない。
- BrowserTestKitの `terminal_browser` に `--format` を足すこと。TTYラッパであり、構造化stdoutは契約にしない。
- DRAgoWingのHTML描画器へ `--format json` を強制すること。一部のJSON入出力と `--forms` は既にある。
- TalkCompress G9〜G14を実装義務にすること。Anthropicのcontext editingやサーバー側要約などは参考に留め、ローカル・キーなしの方針と切り離す。参考: [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)、[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)、[Explore the context window](https://code.claude.com/docs/en/context-window)
- GPU/NPUでの推論評価。機材と比較基準を用意できるまで行わない。
- GGUF測定へのcold/warm時間・ピークRSSの追記。ベンチのコードと再現手順は残るが、今回の環境はDockerを起動できず再実行していない。圧縮をproxyへ組み込むと決めたときに測る。
- labの推論キャッシュ（完全一致・近似応答・再計算・エンジン固有KV）の区別と、TTL・失効・上限の横断検証。今回は照合していない。扱う時期は上と同じ。
- mcp-toolcall-labのstub-front構想（Wikipedia MCP接続、ブラウザOpenAI往復など）を作業項目化すること。リンク管理のみ。[stub-front-roadmap](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/main/docs/stub-front-roadmap.md)
- LittleBitの未閲覧データでの独立確認。探索と年別バックテストは済み。未閲覧データを取る計画はない。
- R001のF4（段階別遷移の実装・26テスト・集約証跡の取り込み）。引き継ぎパッケージが残っていない。計画は Aoi の `STAGE_TRANSITIONS_PLAN.md` に残るので、実装し直すと決めたときはそこから始める。
- LibreChat / Open WebUIへのopt-in接続をラボ中核の完了条件にすること。メモは残すが必須にしない。

<a id="remaining"></a>

## 残件

<a id="pages-32"></a>

### 公開リポジトリのPages展開

全公開・非アーカイブリポジトリへのPages展開は、2026年11月22日に29件すべてで完了した（[済み分](#pages-32-done)）。完了条件としては維持し、リポジトリが増えたときに同じ形で入れる。

- [ ] 入力を持つ既存のサイトで、入力・結果・実行場所を記録し、スマートフォンだけで代表操作を完了できることを確認する。紹介だけのページは対象外。
  - 11月23日: ポートフォリオの2つのツールは確認済み。公開サイト（main `4b88aca`）に対する受入テストが72件成功した（[実行記録](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/actions/runs/45120652946)、GitHub Actions 上の Chromium と WebKit）。HTML取り込みは幅390・タッチありの設定で「貼り付け→抽出→条件変更→ダウンロード→消去」が通る。実機ではなくエミュレーション。
  - 残り: 入力欄を持つほかの公開ページ（DRAgoWing のヒートマップ、OnTautoLogy の `site/`、親の監査ページ `site/ui/`、PySaiyanWeBoo の `index.html`）はスマートフォン幅での操作を確認していない。
- [x] 移管先の公開後に旧リンク・query・hashを検証する。入力の保存は利用者の選択とし、公開用の合成fixtureと分ける。
  - 11月23日: 上と同じ[実行記録](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/actions/runs/45120652946)で確認した。ポートフォリオのトップから旧ツールと新ツールへのリンクが見える。移管した mcp-toolcall-lab のページは `?view=niconico` と `#wiki`・`#niconico` で同じ画面が開く。HTML取り込みは入力を保存しない（操作後も `localStorage` と `sessionStorage` は0件）。テストの入力は `tests/fixtures/html-import/cases.json` の合成データで、利用者の入力とは別。

[元Issueと受入条件](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/issues/32)

<a id="aoi-30"></a>

### Aoiの研究記録

- [x] 研究登録の機械値を英語（`posthoc` / `preregistered` / `unknown`）にし、日本語表示は別ラベルにする。現行の `registration_status()` は「事後構成／事前登録／不明」を返す表示用ヘルパー。Aoiで小さなPRを予定する（この文書PRの範囲外）。
  - 11月22日: 子の QueRyu に `registration_code`（`posthoc` / `preregistered` / `unknown`）を足し、QueRyu の main に入った（[QueRyu #52](https://github.com/myon-bioinformatics/QueRyu/pull/52)）。表示ラベルを返す `registration_status` と、結果行の `registration` の日本語の値は変えていない。残りは Aoi の `queryu/` へのsubtree同期。
  - 11月23日: Aoi の `queryu/` へ squash なしの `git subtree pull` で同期した（QueRyu main `fbfdb7b`、[Aoi #241](https://github.com/myon-bioinformatics/Aoi/pull/241)）。同じバイトを写していた [Aoi #239](https://github.com/myon-bioinformatics/Aoi/pull/239) も #241 に含めた。Aoi main `cd9589c` に入る前の #241 の head で CI 33件成功（pytest は Python 3.11〜3.15）。残りの5つの子も同じ PR で取り込んだ（PythDRagoras は #58 の手前 `fbd948c`。親の pin の切り替えがそろうのを待つ）。
  - 着手時の注意（11月22日に確認）: 実装は子リポジトリ QueRyu の `ask/ask.py` にあり、Aoi 側はそのsubtree。結果行の `registration` は NagoyAction の受付テストが日本語の値で検査し、DRAgoWing は表示ラベルとして使う。子で先に変えてから Aoi へ同期する順になる。既存の値は残し、英語の機械値を別フィールドで足す形なら、利用側を壊さずに分離できる。

[元Issueと受入条件](https://github.com/myon-bioinformatics/Aoi/issues/30)

<a id="btk-117"></a>

### BrowserTestKitの操作

- [ ] 子フレームとopen shadow rootを区別し、再描画後はlocatorを再取得する。現行は件数を比較するが、中身は `not_entered`。 中へ入るときに何を出すか（件数だけか、中の操作対象までか）は未決（11月23日）。 件数と一覧のテスト9件は main `f974ca0` で成功（ローカル確認）。BrowserTestKit側にも残件として記録した（[BrowserTestKit #185](https://github.com/myon-bioinformatics/BrowserTestKit/pull/185)）。

移動後のセッション再観測、headed、任意のStagehandとAndroidは実行範囲を明記する。操作CLIの契約安定後にtransportの要否を判断する。Stagehandは必須にしない。 それぞれの再開条件は BrowserTestKit の `docs/page-inspect-platform-scenarios.md` に記録した（#185）。

[元Issueと受入条件](https://github.com/myon-bioinformatics/BrowserTestKit/issues/117)

<a id="cli-29"></a>

### 横断CLIの引数・出力とテスト

P1.4〜P1.10の残りとP2〜P4は、2026-10-23の調査をそのまま実装せず、現行ソースで再確認してから進める。

- [ ] エージェントが実際に呼ぶ面だけ、`--format json` を正、`--json` を糖衣として足す。対象の候補はNagoyActionの残りサブコマンド、PySaiyanWeBooのhelp整備、gh_identityのhelp穴埋め（`--format` の既存意味と衝突させない）。`uv run pytest` を `python -m pytest` に強制しない。
  - 実行による確認（11月22日、ローカル確認）: NagoyAction `fff1cde`、BrowserTestKit `f974ca0`、PySaiyanWeBoo `9f5a730`、gh_identity `3111c17` の入口とサブコマンド計127件のうち126件で、`--help` は終了0・標準出力、不明な引数は終了2・標準エラー。例外の `btk.py check-png` は存在しないファイルにtracebackを出していた（[BrowserTestKit #185](https://github.com/myon-bioinformatics/BrowserTestKit/pull/185) で修正）。ヘルプ文のない引数は概算でgh_identity 260（88サブコマンド中74）、btk.py 53（18中11）、nagoyaction.py 8（9中2）。旧Issueが名指しした `twin_parity.py`、`collection-cli.cjs`、`tool/flow_diagram.py`、`web_ui.py` は現行mainにない。
  - 11月23日: Aoi の子の単一ファイル入口で `help=` のない `add_argument` を埋めた（AST で数えて、nagoyaction.py 39→0 [NagoyAction #63](https://github.com/myon-bioinformatics/NagoyAction/pull/63)、queryu.py 11→0 [QueRyu #53](https://github.com/myon-bioinformatics/QueRyu/pull/53)、blueprobe.py 7→0 [BlueProbe #63](https://github.com/myon-bioinformatics/BlueProbe/pull/63)、sakanalytics.py 6→0 [SakAnalytics #55](https://github.com/myon-bioinformatics/SakAnalytics/pull/55)、dragowing.py 1→0 [DRAgoWing #66](https://github.com/myon-bioinformatics/DRAgoWing/pull/66)）。pythdragoras.py（9件）は [PythDRagoras #57](https://github.com/myon-bioinformatics/PythDRagoras/pull/57) で埋めたが、ファイルのバイトが Aoi の LittleBit holdout の来歴（`holdout.provenance.json`）の SHA-256 とずれたため [#59](https://github.com/myon-bioinformatics/PythDRagoras/pull/59) で取り消した。来歴の再記録が承認されたら入れ直す。続けて gh_identity.py 332→0（[gh_identity #131](https://github.com/myon-bioinformatics/gh_identity/pull/131)、88 サブコマンドの `--help` が終了0、pytest 1325件成功）、Ironmate の15本 87→0（[Ironmate #252](https://github.com/myon-bioinformatics/Ironmate/pull/252)、CI 22件成功）。エージェントが呼ぶ面（NagoyAction・gh_identity・PySaiyanWeBoo・btk.py）は 0 になった。
  - 11月23日（続き）: 残りも埋めた。親の15本 198→0（[#345](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/345)）、TopOntoLogy 26（[#21](https://github.com/myon-bioinformatics/TopOntoLogy/pull/21)）、OneLaunchNumerousModules 21（[#25](https://github.com/myon-bioinformatics/OneLaunchNumerousModules/pull/25)）、TaxOntOy 19（[#18](https://github.com/myon-bioinformatics/TaxOntOy/pull/18)）、OnTautoLogy 17（[#47](https://github.com/myon-bioinformatics/OnTautoLogy/pull/47)）、ascii_artist 8（[#87](https://github.com/myon-bioinformatics/ascii_artist/pull/87)）、xprobe 7（[#65](https://github.com/myon-bioinformatics/xprobe/pull/65)）、search_seq_including_spaces 7（[#90](https://github.com/myon-bioinformatics/search_seq_including_spaces/pull/90)）、Aoi 4（[#243](https://github.com/myon-bioinformatics/Aoi/pull/243)）、yourself 4（[#75](https://github.com/myon-bioinformatics/yourself/pull/75)）、memo 4（[#6](https://github.com/myon-bioinformatics/memo/pull/6)）、ONTO 3（[#15](https://github.com/myon-bioinformatics/ONTO/pull/15)）、cli_args 2（[#63](https://github.com/myon-bioinformatics/cli_args/pull/63)）、Err-o-RoadRunner 2（[#80](https://github.com/myon-bioinformatics/Err-o-RoadRunner/pull/80)）、markdown 1（[#163](https://github.com/myon-bioinformatics/markdown/pull/163)）。ルート直下の .py を AST で数えた残りは cli_args の1件（宣言表の行で、意図して残す）と pythdragoras.py の9件（上記の来歴の承認待ち）だけ。
  - 11月23日（形式）: NagoyAction で `--format text|json` を足し、`--format json` を正、`--json` を短い書き方にした（親・`doctor`・`show`・`run`・`linkcheck`、[NagoyAction #67](https://github.com/myon-bioinformatics/NagoyAction/pull/67)）。`--json` だけを持つ残りは TalkCompress 5、BlueProbe 3、SakAnalytics 2、QueRyu 1、親 7（`add_argument` の数）。gh_identity は `commit-files` の `--format` が別の意味を持つので足していない。

[元Issueと受入条件](https://github.com/myon-bioinformatics/cli_args/issues/29)

<a id="compression-40"></a>

### TalkCompressの圧縮と往復評価

- [x] G4の品質gate（正答率0.9）の有効化を検討する。測定自体は完了済み。採用fixtureとgateは、この文書の更新では変更しない。
  - 11月23日の判断: 有効化しない。実測では採用18形のうち0.9に達した形が0件（チェコ語3/72、オランダ語3/72、ポーランド語0/18、Qwen2.5-1.5B、[測定記録](https://github.com/myon-bioinformatics/TalkCompress/blob/main/docs/measurements/g4-live.md)）。いま有効にすると採用形がすべて落ち、1つのモデルの結果で採用を決めることになる。再開条件: 同じ手順で0.9に達する形が出たとき、または本番の選定に使うモデルが決まったとき。

[元Issueと受入条件](https://github.com/myon-bioinformatics/TalkCompress/issues/40)
