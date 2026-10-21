# リポジトリ横断の用途と接続候補

2026-10-21に確認できた公開・非アーカイブの23リポジトリを対象とする。
「各リポジトリに最低1つ役立つ用途がある」を探索の仮説とし、既存入口と接続候補を記録する。
機械可読な観測は [cross-repository-use-cases.json](cross-repository-use-cases.json)。

## 採用の判断

- 作業の手順を短くする、重複を減らす、対象の取り違えを防ぐ、のいずれかを実例で確かめる。
- 既存の入口を再利用する。新しいCLIを一律に追加する必要はない。
- 前提確認、対象選択、実行、検証、整形のどの段に接続するかを明示する。
- 未確認・選択範囲・打ち切りを結果に残す。部分評価を全体成功に読み替えない。
- この一覧は利用候補の記録であり、vendor catalogや採用lockの変更ではない。

## 用途一覧

作業ディレクトリ・依存・入力条件は各READMEに従う。DRAgoWingの`python -m dragowing`はREADME記載の親ディレクトリ配置を前提とする。`<...>`は利用者が指定する入力で、そのまま実行する文字列ではない。
23リポジトリ全コマンドを実行したという記録ではない。READMEのblob SHAと検証状態をJSONに残した。

| リポジトリ | 使う場面 | 既存・追加する入口 | 次の接続 |
| --- | --- | --- | --- |
| [Aoi](https://github.com/myon-bioinformatics/Aoi/blob/main/README.md) | 研究パイプラインの入力・変数・実行予定を確認する | `python nagoyaction/nagoyaction.py doctor cycles/c001-chunichi/pipeline.toml` | 子プロジェクトごとの依存調査へ対象パスを渡す |
| [ascii_artist](https://github.com/myon-bioinformatics/ascii_artist/blob/main/README.md) | 端末やテキスト報告に処理関係を描く | `diagram(...) / render_diagram(...)` | 選択した実行計画を軽量な文字図へ整形する |
| [BlueProbe](https://github.com/myon-bioinformatics/BlueProbe/blob/main/README.md) | 取得済みHTMLから構造と未抽出箇所を記録する | `blueprobe.py / HtmlSource / inspect_queryu_cache` | QueRyuの選択キャッシュを観測入力へ渡す |
| [browser-test-kit](https://github.com/myon-bioinformatics/browser-test-kit/blob/main/README.md) | ページ・環境の対象選択を比較と要約まで通す | `scripts/app_comparison.py / scripts/app_digest.py --list` | Ironmateの選択APIを呼び、対象外PNGをデコードしない |
| [cli_args](https://github.com/myon-bioinformatics/cli_args/blob/main/README.md) | 引数検証と実行予定のdry-runを共通化する | `python -S examples/run_cli.py --format json --dry-run -- git status --short` | 選択した対象を既存コマンドへ明示的に渡す |
| [DRAgoWing](https://github.com/myon-bioinformatics/DRAgoWing/blob/main/README.md) | 保存結果をID・列・条件で選んで表示する | `python -m dragowing --results <DIR> --out <OUT> --id <ID>` | 選択結果をweb-uiやMarkdownの報告へ渡す |
| [Err-o-RoadRunner](https://github.com/myon-bioinformatics/Err-o-RoadRunner/blob/main/README.md) | 失敗時だけstderr・終了コード・JUnitをまとめる | `python3 -S triage.py digest <artifacts.zip> --limit 5` | 選んだ失敗証跡をghiのrun/job識別子と一緒に渡す |
| [flutter_navigation_basic](https://github.com/myon-bioinformatics/flutter_navigation_basic/blob/main/README.md) | 画面の責務・到達性・未分類状態を抽出する | `python/pattern_responsibility_audit.py --json` | 完全性監査を維持したうえで結果表示に状態・画面番号の選択を加える |
| [gh_identity](https://github.com/myon-bioinformatics/gh_identity/blob/main/README.md) | checkout・Git/gh有無・対象PR/SHA/CIを確認する | `python -S gh_identity.py capabilities / overview / git-status` | 実行対象の同一性と前提観測を他ツールへ渡す |
| [Ironmate](https://github.com/myon-bioinformatics/Ironmate/blob/main/README.md) | ページ・環境を選んで画像比較シートを生成する | `python -S browser_matrix.py <ROOT> --page 'flutter-*' --env 'Linux-*' --list` | browser-test-kitの比較・要約から同じ選択を渡す |
| [markdown](https://github.com/myon-bioinformatics/markdown/blob/main/README.md) | 必要な変換ケースだけ往復変換・差分比較する | `scripts/converter_parity_report.py / scripts/converter_contract_audit.py --list` | 種別付きcase keyで選択し、部分評価の範囲を報告へ残す |
| [mcp-toolcall-lab](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/main/README.md) | offline fixtureを名前で選んで再生する | `python -m mcp_toolcall_lab.prompt_experiment replay --list` | 両replayで共通選択を使い、一覧時は読込・再生・出力をしない |
| [myon-bioinformatics](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/main/README.md) | 用途・入口・接続先・検証状態を継続して照合する | `vendor-catalog.json / vendor-consumers.json / 本一覧` | 各リポジトリの利用候補と実装PRを一か所から追跡する |
| [myon-bioinformatics.github.io](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/main/README.md) | 取り込んだ実験ページとプロジェクトの入口を集約する | `python -S scripts/import_lab_pages.py --source <EXPORT>` | 検証済み成果物への導線をプロジェクト検索に載せる |
| [NagoyAction](https://github.com/myon-bioinformatics/NagoyAction/blob/main/README.md) | 定義済み処理を実行前にdoctor/showで確認する | `python nagoyaction.py doctor <pipeline.toml>` | ghiとyourselfの前提観測を実行前に参照する |
| [nvd_nist_known_vulns](https://github.com/myon-bioinformatics/nvd_nist_known_vulns/blob/main/README.md) | 明示されたCPEとバージョンでCVE情報を取得する | `python nvd_nist_known_vulns.py --cpe '<CPE23>'` | 前提観測と明示CPE対応表を照合する。パッケージ名だけでCVEを断定しない |
| [PythDRagoras](https://github.com/myon-bioinformatics/PythDRagoras/blob/main/README.md) | 条件候補を比較し反例を残す | `pythdragoras.py / littlebit_approx.py` | 選択した結果データから条件候補を評価する |
| [QueRyu](https://github.com/myon-bioinformatics/QueRyu/blob/main/README.md) | キャッシュをgroup/key/timeで選んで共有する | `python cache_share.py --cache <CACHE> query --group <GROUP>` | 再取得せず選択キャッシュをBlueProbeへ渡す |
| [SakAnalytics](https://github.com/myon-bioinformatics/SakAnalytics/blob/main/README.md) | 観測JSONLと設定から指標を作る | `python sakanalytics.py --games <JSONL> --config <TOML> --out <OUT>` | 汎用設定に従って選択観測を集計する |
| [search_seq_including_spaces](https://github.com/myon-bioinformatics/search_seq_including_spaces/blob/main/README.md) | 形式・圧縮拡張子・名前から配列候補を選ぶ | `python -S sequence_tool.py discover <ROOT> --format fasta --pattern 'sample*'` | 候補JSONを既存の読み取り・検証・解析コマンドへ渡す |
| [web-ui](https://github.com/myon-bioinformatics/web-ui/blob/main/README.md) | 共通UIとHTML生成を報告画面へ再利用する | `from web_ui import render_document` | 選択済みの表・グラフ・報告を一枚のHTMLへ表示する |
| [xprobe](https://github.com/myon-bioinformatics/xprobe/blob/main/README.md) | ファイル・Pythonシンボル・設定を規則で探す | `python -S xprobe.py --symbols <ROOT> --symbol-pattern '*Adapter' --brief` | 候補を静的に発見し、実行時登録や完全な動作証明とは区別する |
| [yourself](https://github.com/myon-bioinformatics/yourself/blob/main/README.md) | OS・Python・runnerの観測値を確認する | `yourself.collect() / python yourself.py --runner-env --runner-env-json` | ghiのGitHub観測と組み合わせ、ローカルとCIの環境差を確認する |

## 今回の実装と実行確認

- **browser-test-kit**: 比較・要約へ`--page`・`--env`・`--list`を接続。既定のFlutter対象は維持する。選択計画で分母を作り、対象外の壊れたPNGと重複を画像処理前に除外する。64件の関連テストとactionlintが成功。実Ironmate実装との`python -S`確認で、非FlutterページとLinux 1環境の比較・要約が1/1となった。
- **mcp-toolcall-lab**: 2つのoffline replayへ共通の`--case`・`--exclude`・`--list`を接続。既定出力と判定は維持する。57件の関連テストが成功。一覧はfixture本文の読込も再生もせず、実provider APIは呼び出さない。
- **markdown**: 2つの変換評価へ共通のケース選択を接続。25件の関連テストが成功。既定JSONは変更前と一致し、部分評価だけ選択範囲を追加する。parityの部分評価は`selected_cases_only`とする。
- **gh_identity**: 既存`capabilities`を`python -S`で実行し、利用可能性とバージョンを観測できた。`overview`と`split-preflight`も既存入口として文書を確認した。追加の同等コマンドは作らない。

実装PR: [browser-test-kit #110](https://github.com/myon-bioinformatics/browser-test-kit/pull/110)、[mcp-toolcall-lab #148](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/148)、[markdown #117](https://github.com/myon-bioinformatics/markdown/pull/117)。独立レビュー済みで最終headのCI確認中。その他の接続候補は、利用場面を確認した段階であり実装完了ではない。

## 続きの実装候補

1. Aoiの複数の依存調査で、対象パスと除外条件を明示し、選択結果をmin.py化の入力に渡す。
2. Flutterの完全性監査を維持しながら、監査結果から画面番号・責務・未分類状態を抽出できるようにする。
3. ghiとyourselfの既存観測をNagoyActionのdoctorや失敗調査へ渡す。認証・通信の失敗と未確認を成功に扱わない。
4. QueRyuの選択キャッシュ、BlueProbeの観測、SakAnalytics/PythDRagorasの解析、DRAgoWingの表示を、既存の入出力契約で接続する。

