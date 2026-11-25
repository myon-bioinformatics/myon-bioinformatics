# 依存削減・環境軽量化の週次計測台帳

初回記録 2026-11-25（JST）。対象は myon-bioinformatics 配下の公開リポジトリで、関連PRと計測ログを確認できたものを記録する。

削減率は `(変更前 − 変更後) ÷ 変更前 × 100`。負の値は悪化を示す。複数試行の時間は原則として中央値を使う。単一試行の時間は参考値とする。計測基準として、リポジトリ、機能・プロファイル、指標の定義、実行条件をそろえた過去の行を参照する。

## 初回確認（2026-11-18〜11-25 JST）

| 日付（JST、実測日またはPR記録日） | リポジトリ・対象 | 比較条件 | 導入パッケージ数（削減率） | 環境容量（削減率） | セットアップ時間（削減率） | 判定・補足 | 根拠 |
|---|---|---|---:|---:|---:|---|---|
| 11-23 | Aoi・研究機能 | 開発用 → 機能限定。Ubuntu 24.04 x86_64、Python 3.12.3、uv 0.13.9、同一ランナー・同一lock・個別の新規キャッシュ。インストール各1回、機能限定を先に実行 | 18 → 2 件（88.9%） | 265,582,555 → 191,678,301 B（27.8%） | 0.942773 → 0.730919 秒（22.5%） | 機能限定プロファイル比較。対象コミットは `1d54d999640a5b2a1e56e41abab1039ad8e86aff`。時間は `uv pip install` のみで参考値 | [Aoi #238](https://github.com/myon-bioinformatics/Aoi/pull/238) / [CI run](https://github.com/myon-bioinformatics/Aoi/actions/runs/45094876674) / [result.json・install.log](https://github.com/myon-bioinformatics/Aoi/actions/runs/45094876674/artifacts/14782783203) |
| 11-23 | ascii_artist・依存グループ | 未参照の `stdlib-test` グループを削除 | 未計測 | 未計測 | 未計測 | PRで導入環境の前後計測を確認できず | [ascii_artist #86](https://github.com/myon-bioinformatics/ascii_artist/pull/86) |
| 11-23 | DRAgoWing・依存グループ | 未参照の `stdlib-test` グループを削除 | 未計測 | 未計測 | 未計測 | PRで導入環境の前後計測を確認できず | [DRAgoWing #67](https://github.com/myon-bioinformatics/DRAgoWing/pull/67) |
| 11-23 | FluidFullSpeedSimulation・依存グループ | 未参照の `stdlib-test` グループを削除 | 未計測 | 未計測 | 未計測 | PRで導入環境の前後計測を確認できず | [FluidFullSpeedSimulation #39](https://github.com/myon-bioinformatics/FluidFullSpeedSimulation/pull/39) |
| 11-23 | markdown・依存グループ | 未参照の `stdlib-test` グループを削除 | 未計測 | 未計測 | 未計測 | PRで導入環境の前後計測を確認できず | [markdown #162](https://github.com/myon-bioinformatics/markdown/pull/162) |
| 11-23 | PySaiyanWeBoo・依存グループ | 未参照の `stdlib-test` グループを削除 | 未計測 | 未計測 | 未計測 | PRで導入環境の前後計測を確認できず | [PySaiyanWeBoo #164](https://github.com/myon-bioinformatics/PySaiyanWeBoo/pull/164) |

## 過去の比較基準（2026年11月）

† は初回整理で計測日を特定できなかった行のPR日付。ソースやプロファイルが異なる行は、それぞれの比較範囲で読む。

| 日付（JST、実測日またはPR記録日） | リポジトリ・対象 | 比較条件 | 導入パッケージ数（削減率） | 環境容量（削減率） | セットアップ時間（削減率） | 判定・補足 | 根拠 |
|---|---|---|---:|---:|---:|---|---|
| 11-05 | NagoyAction・localci | Aoiの開発依存 → localci専用。Linux x86_64、Python 3.12.15、uv 0.13.5 | 18 → 5 件（72.2%） | 267,981,866 → 8,667,505 B（96.8%） | 未計測 | 比較可能なセットアップ時間は未計測。実行時間中央値は1.701 → 1.755秒（3.2%悪化） | [NagoyAction #40](https://github.com/myon-bioinformatics/NagoyAction/pull/40) / [計測ログ](https://github.com/myon-bioinformatics/NagoyAction/blob/cdfe07541e169e346eb558465a8ddde7077cbce6/docs/nano-localci-measurement.json) |
| 11-05† | BrowserTestKit・Docker/Bun＋Node | 同一ホスト・固定入力、3対。依存準備済みDockerイメージと毎回オフライン展開するnano | 未計測 | 比較不可 | 比較不可 | 共通のセットアップ境界なし。テスト中央値は Docker 1.771 → nano 1.769秒。実行経路は Docker 2.398 → nano 3.132秒（30.6%悪化） | [BrowserTestKit #149](https://github.com/myon-bioinformatics/BrowserTestKit/pull/149) / [CI run](https://github.com/myon-bioinformatics/BrowserTestKit/actions/runs/41955055837) |
| 11-03 | Ironmate・Base64テスト | 開発用 → `--only-group test --exact`。Linux x86_64、Python 3.12.14、同一lock、各1回、テスト後の通常ファイル論理サイズ | 13 → 6 件（53.8%） | 290,355,508 → 11,567,453 B（96.0%） | 未計測 | 比較可能なセットアップ時間は未計測。テストは両方29件成功 | [Ironmate #199](https://github.com/myon-bioinformatics/Ironmate/pull/199) / [計測ログ](https://github.com/myon-bioinformatics/Ironmate/blob/c483791a854b541295dbcd6c86a1e293b36d492d/docs/nano-base64-tests.md) |
| 11-03† | mcp-toolcall-lab・chat-stub契約 | `--extra test` → `--only-group ci-stdlib --exact`。Linux、Python 3.12.15、同一lock・個別の空キャッシュ、各1回 | 76 → 10 件（86.8%） | 64,001,491 → 10,782,288 B（83.2%） | 8.326 → 2.437 秒（70.7%） | プロファイル比較。容量はテスト後。時間は単一ペアの参考値 | [mcp-toolcall-lab #191](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/191) |
| 11-02† | PySaiyanWeBoo（旧web-ui）・既定 | main → PRの既定 `uv sync`。Linux x86_64、Python 3.14、Node 24.22.2、計測uv 0.11.32、新規キャッシュ、各1回 | 24 → 7 件（70.8%） | 23,020,089 → 9,468,459 B（58.9%） | 4.47 → 1.40 秒（68.7%） | コード変更前後。件数はプロジェクト自身を含む | [PySaiyanWeBoo #105](https://github.com/myon-bioinformatics/PySaiyanWeBoo/pull/105) |
| 11-02† | PySaiyanWeBoo（旧web-ui）・Gradio | main → PRの `uv sync --group gradio`。既定と同じホスト、ツール、キャッシュ条件で各1回 | 65 → 60 件（7.7%） | 390,345,641 → 386,265,151 B（1.0%） | 11.97 → 11.52 秒（3.8%） | コード変更前後。件数はプロジェクト自身を含む | [PySaiyanWeBoo #105](https://github.com/myon-bioinformatics/PySaiyanWeBoo/pull/105) |
| 11-02† | Ironmate・npm実行用 | 通常 `npm ci` → `npm ci --omit=dev`。Node 24.22.2、クリーンな導入先、npmキャッシュあり、各3回 | 196 → 18 件（90.8%） | 59.2 → 8.0 MiB（86.5%） | 3.12 → 0.57 秒（81.7%） | 導入プロファイル比較。旧版と同じ依存集合を通常プロファイルで計測 | [Ironmate #188](https://github.com/myon-bioinformatics/Ironmate/pull/188) |
| 11-01 | DRAgoWing・HTML | 通常 → HTML/Markdown限定。Linux x86_64、Python 3.12.14、uv 0.13.4、新規キャッシュ | 9 → 1 件（88.9%） | 254,997,482 → 374,693 B（99.9%） | 13.135464 → 16.519192 秒（-25.8%・悪化） | 2ペア完了。3ペア目はベースラインのPyPI接続タイムアウト | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/b8deca83c3ced32c5ac1e24e68e36cb3bc546a4c/docs/nano-feature-environments.md) |
| 11-01 | Ironmate・PPTX | 通常 → PPTX限定。Linux x86_64、Node 24.22.1、npm 11.9.0、新規キャッシュ、3ペア | 198 → 18 件（90.9%） | 40,035,745 → 6,949,782 B（82.6%） | 8.287560 → 4.626664 秒（44.2%） | プロファイル比較。件数はインストール済みパッケージのパス数 | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/b8deca83c3ced32c5ac1e24e68e36cb3bc546a4c/docs/nano-feature-environments.md) |
| 11-01 | mcp-toolcall-lab・SDKクライアント | 通常 → クライアント限定。Linux x86_64、Python 3.12.14、uv 0.13.4、新規キャッシュ、3ペア | 69 → 28 件（59.4%） | 58,981,931 → 30,223,954 B（48.8%） | 9.848 → 7.994 秒（18.8%） | SDKクライアントの計測。サーバー込みでは58,981,931 → 89,205,885 B（51.2%増） | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/39639f5bbca9d6a79e934651a983d5a990b98e5b/docs/nano-client-mirror-environments.md) |
| 11-01 | BrowserTestKit・文書ミラー | 通常 → 文書ミラー限定。Linux x86_64、Node 24.22.1、npm 11.9.0、新規キャッシュ、3ペア | 221 → 30 件（86.4%） | 61,017,247 → 25,424,737 B（58.3%） | 8.343 → 7.253 秒（13.1%） | 導入中央値は短縮。第1ペアは9.687 → 14.984秒（54.7%悪化） | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/39639f5bbca9d6a79e934651a983d5a990b98e5b/docs/nano-client-mirror-environments.md) |
| 11-01 | markdown・GradioテキストAPI | 複数フロントエンド → Gradio限定。Linux x86_64、Python 3.12.14、uv 0.13.4、新規キャッシュ | 72 → 52 件（27.8%） | 642,781,043 → 239,130,663 B（62.8%） | 17.575416 → 10.444845 秒（40.6%） | 1ペア完了。2ペア目のGradio側はタイムアウト。時間は参考値 | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/68f79e8d229d79778050866374880852a49437f3/docs/nano-gradio-environment.md) |
| 11-01 | Aoi・親のオフラインexport | 通常 → 機能限定のrequirements行数 | 未計測 | 未計測 | 未計測 | 参考。20 → 12はマーカーを含むrequirements行数 | [親 #195](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/195) / [計測ログ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/b8deca83c3ced32c5ac1e24e68e36cb3bc546a4c/docs/nano-feature-environments.md) |
