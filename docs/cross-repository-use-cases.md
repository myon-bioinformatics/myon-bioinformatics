# リポジトリ横断の用途と検証状態

2026-10-21に観測した公開・非アーカイブ23リポジトリの役割を記録する。
前波の3件に続き、残り20件も実装または既存の入出力を使うオフライン検証へ進めた。
全コマンド・ライブサービス・自動接続の完了を意味しない。手動の橋渡しと未確認範囲も記録する。

機械可読な観測は [cross-repository-use-cases.json](cross-repository-use-cases.json)、
再現するテストと接続の証跡は [検証記録](cross-repository-verification-20261021.md)。
README blobは前波時点の観測であり、最新版の保証ではない。現行mainを照合したものはJSONの`observed_head`に記録した。

## 用途と接続

作業ディレクトリ・依存・入力条件は各READMEに従う。`<...>`は利用者の入力を表す。
DRAgoWingのmodule実行はREADME記載の親ディレクトリ配置を前提とする。

| リポジトリ | 使う場面 | 入口 | 接続と範囲 |
| --- | --- | --- | --- |
| [Aoi](https://github.com/myon-bioinformatics/Aoi/blob/main/README.md) | 子プロジェクトのPythonソースを選び依存の利用面を調べる | `python -S scripts/survey_third_party.py --match 'blueprobe/*' --list` | 全rootの依存宣言とlocal名を維持して選択ソースを既存surveyへ渡す |
| [ascii_artist](https://github.com/myon-bioinformatics/ascii_artist/blob/main/README.md) | 端末やテキスト報告に処理関係を描く | `diagram(...) / render_diagram(...)` | 選択した実行計画を軽量な文字図へ整形する |
| [BlueProbe](https://github.com/myon-bioinformatics/BlueProbe/blob/main/README.md) | 取得済みHTMLから構造と未抽出箇所を記録する | `blueprobe.py / HtmlSource / inspect_queryu_cache` | QueRyuの選択キャッシュを観測入力へ渡す |
| [browser-test-kit](https://github.com/myon-bioinformatics/browser-test-kit/blob/main/README.md) | ページ・環境の対象選択を比較と要約まで通す | `scripts/app_comparison.py / scripts/app_digest.py --list` | Ironmateの選択APIを呼び、対象外PNGをデコードしない |
| [cli_args](https://github.com/myon-bioinformatics/cli_args/blob/main/README.md) | 引数検証と実行予定のdry-runを共通化する | `python -S examples/run_cli.py --format json --dry-run -- git status --short` | 選択した対象を既存コマンドへ明示的に渡す |
| [DRAgoWing](https://github.com/myon-bioinformatics/DRAgoWing/blob/main/README.md) | 保存結果をID・列・条件で選んで表示する | `python -m dragowing --results <DIR> --out <OUT> --id <ID>` | 選択結果を既存HTML表示と独自Markdown報告へ渡す。追加web-ui adapterは未検証 |
| [Err-o-RoadRunner](https://github.com/myon-bioinformatics/Err-o-RoadRunner/blob/main/README.md) | 失敗時だけstderr・終了コード・JUnitをまとめる | `python3 -S triage.py digest <artifacts.zip> --limit 5` | 選んだ失敗証跡をghiのrun/job識別子と一緒に渡す |
| [flutter_navigation_basic](https://github.com/myon-bioinformatics/flutter_navigation_basic/blob/main/README.md) | 画面の責務・到達性・未分類状態を抽出する | `python -S python/pattern_responsibility_audit.py --pattern '00?' --json --check` | 全198件監査後にID・状態・責務の表示を絞る。全体のerrorsとcheck合否を保持する |
| [gh_identity](https://github.com/myon-bioinformatics/gh_identity/blob/main/README.md) | checkout・Git/gh有無・対象PR/SHA/CIを確認する | `gh_identity.capabilities() / git_status() via parent preflight.py` | 親の採用済みghiでGit/gh/Python/tokenの存在とworktree状態を観測する |
| [Ironmate](https://github.com/myon-bioinformatics/Ironmate/blob/main/README.md) | ページ・環境を選んで画像比較シートを生成する | `python -S browser_matrix.py <ROOT> --page 'flutter-*' --env 'Linux-*' --list` | browser-test-kitの比較・要約から同じ選択を渡す |
| [markdown](https://github.com/myon-bioinformatics/markdown/blob/main/README.md) | 必要な変換ケースだけ往復変換・差分比較する | `scripts/converter_parity_report.py / scripts/converter_contract_audit.py --list` | 種別付きcase keyで選択し、部分評価の範囲を報告へ残す |
| [mcp-toolcall-lab](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/main/README.md) | offline fixtureを名前で選んで再生する | `python -m mcp_toolcall_lab.prompt_experiment replay --list` | 両replayで共通選択を使い、一覧時は読込・再生・出力をしない |
| [myon-bioinformatics](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/main/README.md) | 用途・入口・接続先・検証状態を継続して照合する | `python -S preflight.py --root . --require git --python-min 3.11` | 採用済みghiの前提確認にyourselfのOS/Python JSONを添え、用途・実装・検証を本一覧へ残す |
| [myon-bioinformatics.github.io](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/main/README.md) | 検証済みlab公開exportを出版ID付きのツール導線へ取り込む | `python -S scripts/import_lab_pages.py --source <EXPORT>` | 既存Tools & Demosから閲覧する。プロジェクト検索への登録はprojects.jsonの手動curationが必要 |
| [NagoyAction](https://github.com/myon-bioinformatics/NagoyAction/blob/main/README.md) | 定義済み処理を実行前にdoctor/showで確認する | `python nagoyaction.py doctor <pipeline.toml>` | ghi/yourselfの環境観測を先に確認し、別途doctorでpipeline固有の不足を検出する |
| [nvd_nist_known_vulns](https://github.com/myon-bioinformatics/nvd_nist_known_vulns/blob/main/README.md) | 明示されたCPEとバージョンでCVE情報を取得する | `python nvd_nist_known_vulns.py --cpe '<CPE23>'` | 明示CPEで照合する入口を利用する。package→CPE対応表は呼出側で用意し、パッケージ名だけでCVEを断定しない |
| [PythDRagoras](https://github.com/myon-bioinformatics/PythDRagoras/blob/main/README.md) | 条件候補を比較し反例を残す | `pythdragoras.py / littlebit_approx.py` | 選択した結果データから条件候補を評価する |
| [QueRyu](https://github.com/myon-bioinformatics/QueRyu/blob/main/README.md) | キャッシュをgroup/key/timeで選んで共有する | `python cache_share.py --cache <CACHE> query --group <GROUP>` | 再取得せず選択キャッシュをBlueProbeへ渡す |
| [SakAnalytics](https://github.com/myon-bioinformatics/SakAnalytics/blob/main/README.md) | 観測JSONLと設定から指標を作る | `python sakanalytics.py --games <JSONL> --config <TOML> --out <OUT>` | 汎用設定に従って選択観測を集計する |
| [search_seq_including_spaces](https://github.com/myon-bioinformatics/search_seq_including_spaces/blob/main/README.md) | 形式・圧縮拡張子・名前から配列候補を選ぶ | `python -S sequence_tool.py discover <ROOT> --format fasta --pattern 'sample*'` | 候補JSONを既存の読み取り・検証・解析コマンドへ渡す |
| [web-ui](https://github.com/myon-bioinformatics/web-ui/blob/main/README.md) | 共通UIとHTML生成を報告画面へ再利用する | `from web_ui import render_document` | 選択済みの表・グラフ・報告を一枚のHTMLへ表示する |
| [xprobe](https://github.com/myon-bioinformatics/xprobe/blob/main/README.md) | ファイル・Pythonシンボル・設定を規則で探す | `python -S xprobe.py --symbols <ROOT> --symbol-pattern '*Adapter' --brief` | 候補を静的に発見し、実行時登録や完全な動作証明とは区別する |
| [yourself](https://github.com/myon-bioinformatics/yourself/blob/main/README.md) | OS・Python・runnerの観測値を確認する | `python -S yourself.py --format json` | collect()のOS/runtime JSONを親preflightへ渡し、hostとdirectoryは破棄する |

## 今回追加した実装

- [Aoi #110](https://github.com/myon-bioinformatics/Aoi/pull/110): 既存の依存surveyに`--match`・`--exclude`・`--list`。選択外のlocal moduleもlocalとして分類し、依存宣言は全rootを参照。8件のテスト成功。blueprobe 34パスを`python -S`で列挙した。他のinventoryやshrink生成の範囲は変更しない。
- [Flutter #217](https://github.com/myon-bioinformatics/flutter_navigation_basic/pull/217): 既存監査に`--pattern`・`--status`・`--owner`。全198件監査とerrors/receiptを維持し表示だけを絞る。34件のテスト成功、4通りの既定CLIの出力と終了値が変更前と一致。除外IDの欠落でも`--check`は失敗する。
- [親 #131](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/131): 採用済みghiとyourself JSONをつなぐ[前提確認](preflight.md)。3件の関連テスト、実`python -S`接続、既存stdlib smokeが成功。深いJSONを終了2で拒否する修正も独立再確認済み。親の全体テストは1294 passed、64 skipped、50 subtests passed。

各実装は別エージェントが独立レビューした。機能CIと最終headを照合し、3件をmainへ統合した。
Copilot自動レビューがサービスエラーの場合はレビュー成功とは扱わない。vendor lock・登録・workflow権限は変更しない。

## 既存入口で確認できた用途

QueRyu→BlueProbe→SakAnalytics、PythDRagorasの保存結果比較、DRAgoWingの独自Markdown報告は既存入出力で実行できた。
この5件は190テスト成功。ascii_artistの文字図、cli_argsの明示argv、失敗digest、静的symbol探索、配列候補から解析、web-uiのHTML生成も確認した。
詳細・固定head・テスト選択・入力の来歴・限界は検証記録とJSONに残した。

portfolioのimportは出版ID付きのツール導線として使える。プロジェクト検索カードは`projects.json`の手動curationで、importerからの自動登録は未実装。
NVDは明示CPEとオフラインの基礎処理まで確認し、package名からの自動CPE推定や実CVE適用判定は行っていない。

## 前波の統合

| 実装 | mainの統合コミット | 成功した機能CI | 既定スキップ |
| --- | --- | --- | --- |
| browser-test-kit #110 | [b353700](https://github.com/myon-bioinformatics/browser-test-kit/commit/b35370057de2d21e0306629787ad3d6668847fee) | 23 | 0 |
| mcp-toolcall-lab #148 | [8a60c11](https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/8a60c116237ede6deeeafbf4bea16e5b3f8845ee) | 8 | 0 |
| markdown #117 | [0c818f1](https://github.com/myon-bioinformatics/markdown/commit/0c818f116659adebaa2d4042d3b356e5f3094f14) | 24 | 2 |

前波はそれぞれ64・57・25件の関連テストと統合後のtree/実コマンドを確認した。Ironmateの比較・要約への接続はbrowser-test-kit #110で完了しており、同じ実装を追加しない。

## 今回の統合記録

| 実装 | main統合コミット | 成功した機能CI | 既定スキップ |
| --- | --- | --- | --- |
| Aoi #110 | [cadc40a](https://github.com/myon-bioinformatics/Aoi/commit/cadc40a4a52eb0165fb98543a089fcfaab5f3359) | 18 | 1 |
| Flutter #217 | [eed4780](https://github.com/myon-bioinformatics/flutter_navigation_basic/commit/eed47808c22282adb54a20b788c64dab4cfa2022) | 13 | 13 |
| 親 preflight #131 | [611e127](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/611e127b38001be4aab5425e259575a8420d2ab6) | 15 | 1 |

統合treeは独立レビューしたheadのtreeと一致。統合後もAoi 8件・Flutter 34件・preflight 3件の関連テストと実コマンドを再確認した。Aoiは一覧34パスとsurvey選択件数が一致し、既存の全体証跡チェックも成功。Flutterは表示2件でも全体198件・errors 0を保持した。preflightは実yourself JSONを受け取り、Git/Python要件を満たしつつsupplied receiptを未検証と表示した。

親mainには#131後に統計画像2ファイルだけの自動更新 [94218fa](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/94218fa5c4f0f2585fbd306435545a00ba5766f8) が入った。本検証記録はその更新後のtreeを基準とし、画像更新を保持した。AoiとFlutterも統合直前にmainと未解決review threadを再確認した。
