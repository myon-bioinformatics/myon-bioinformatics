# 親リポジトリの監査Pages

公開先は https://myon-bioinformatics.github.io/myon-bioinformatics/ 。ポートフォリオのPagesは変更しません。

## 配置と自動更新

`.github/workflows/pages.yml` は関連ファイルのmain push、毎日06:17 JST、workflow_dispatchで検証後に `python3 -S tools/build_audit_pages.py` を実行し、共通Pages workflowから `site/dist` を公開します。スケジュールはGitHub側で遅延する場合があります。初回はリポジトリの Settings → Pages → Source を **GitHub Actions** に設定します。GITHUB_TOKENでのPages初期有効化はGitHub側で許可されないため、管理設定を自動的に変更する処理やPATの追加は行いません。

PRではPython標準unittestとNode 26の組込みテストを実行します。同一リポジトリのPRのみ、公開2リポジトリ（cli_args,xprobe）の実データで読み取り専用のビルドを追加検証します。PRからPages公開は行いません。実行時はUbuntu 24.04標準のPython 3.11以上を使用し、追加のpip/npm依存は不要です。公開jobにのみpages/id-token権限を与え、データ収集に使うGH_TOKENはGITHUB_TOKENを環境変数で渡します。GitHubトークンをブラウザへ送信しません。

## データと失敗時の動作

提供元は既存の `ci_status.py` / vendored `gh_identity.py`。公開・非archived・指定ownerのリポジトリだけを列挙し、open一覧、詳細時点のhead、そのSHAのcheck-runsとcommit statuses、未解決reviewThreadsを取得します。最後にheadとopen状態を再確認します。途中でhead/stateが変わった場合、一覧やレビューのページが欠落した場合、API制限・通信障害の場合は公開を止めます。CI自体が失敗しているPRは正常な観測結果として掲載します。

前回の `data.json` / `history.json` を公開先から読み、404だけを初回公開として許容します。403/429/5xx・壊れたJSON・サイズ超過は失敗します。失敗したビルドはPages artifactのupload/deployへ進まず、前回の公開内容を維持します。再実行はActionsから行えます。

`index.html` はSitesで作成した操作画面、`report.html` は共通web_uiヘルパーによる生成レポートです。すべて相対参照なのでプロジェクトPagesのサブパスでも動作します。最新head、観測日時、CI失敗のcheck名とURLを表示します。再読込ボタンは公開済みJSONを読むもので、GitHub Actionsの新規実行を起動するものではありません。

## 週次記録と手入力

毎週最初の正常生成をISO週・JST基準で `history.json` に保存し、直近13週を次回公開へ引き継ぎます。同じ週の再実行ではその週の基準値を上書きしません。最新の一覧は毎回更新します。完了した項目がopen一覧から消えた場合、ブラウザの完了記録には前回の観測として残し、現行の作業キューには再混入させません。観測一覧からの消失はmerged/closedと断定しません。初回の週次履歴は1件です。

個人の状態・メモ・優先度・完了日・共通化進捗はブラウザのlocalStorageに保存し、観測JSONの更新で上書きしません。SitesとGitHub Pagesは別originのため、Sites画面で「記録を書出し」、Pages画面で「JSON読込」を行うと移行できます。端末間の移行も同じです。共通web_uiレポートのJSON形式は既存 `gh_ops.py records-import` で `site/records.jsonl` に取り込めます。共通化は証拠を確認して手動更新し、タイトルから抽出する監査軸を適合判定と混同しないでください。

## ローカル検証

```sh
python3 -S -m unittest discover -s tests -p test_ci_status.py
python3 -S -m unittest discover -s tests -p test_audit_pages.py
node --test tests/test_audit_model.cjs
node --check site/ui/app.js
# GH_TOKENを環境変数に設定した上で実データを検証する場合
python3 -S tools/build_audit_pages.py --repos cli_args,xprobe --previous '' --previous-history ''
```

全件の公開は `--repos` を指定せず行います。初回公開後はPages workflowの成功と `data.json` のgenerated_atを確認してください。
