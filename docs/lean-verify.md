# Lean 形式検証

共通ラッパー `lean_verify.py` と再利用可能な CI を親リポジトリで管理します。
利用側は証明、固定した `lean-toolchain`、短い CI 呼び出しを管理します。
通常実行の依存は Python 標準ライブラリだけです。Lean は形式検証の専用レーンにのみ
追加し、各リポジトリの通常テストや実行時依存を変更しません。

## 三種類の証跡

| 方法 | 確認する対象 | 限界 |
| --- | --- | --- |
| 数値実験・回帰テスト | 実装の具体的な入出力と既存の期待値 | 調べた入力以外を証明しない |
| 境界条件・反例探索 | 不変条件が壊れる具体例、最小量、上限、端数など | 有限探索で反例がないことは全入力の証明ではない |
| Lean の形式証明 | 明記した仮定から命題が導かれること | Python がその数式どおり動くことは別の課題 |

FFSS の導入例では、Q48 の量を整数として扱う保存則と非負性の命題を証明し、
実際の `Network.step` と `DelayLine.push` を通常の Python テストから検査します。
xprobe を利用するプロジェクトでは同じ接続点の反例探索を拡張できます。
この変更で xprobe 自体を改修したり、Python 全体を形式検証済みとすることはありません。

## ローカルで実行する

Python 3.11 以上で実行します。以下は利用側リポジトリのルートから、隣に配置した
親リポジトリのラッパーを呼び出す例です。

```sh
python -S ../myon-bioinformatics/lean_verify.py \
  --cwd formal \
  --theorem Example.conservation \
  --json-output build/lean-verification.json \
  Conservation.lean
```

- `--cwd`: Lean の作業ディレクトリ。相対的な証明パスと `lean-toolchain` の基準。
- `--theorem`: 公理を監査する宣言名。単一の証明エントリーポイントに対して複数指定可能。
  名前空間を含む ASCII 識別子を受け付け、末尾の `'` も使用可能。
- `--required`: ツール未導入・ツールチェーン利用不可も非ゼロ終了にする。
- `--timeout`: 各サブプロセスの制限秒数。既定値 120、正の有限値。
- `--json-output`: 標準出力と同じ JSON を保存。相対パスは呼び出し元のディレクトリ基準。
- `--lake`: `lake env lean` で実行。Lake のプロジェクト設定とビルド済み依存が必要。

Lean のみを使う場合は、`formal/lean-toolchain` を次のように固定します。

```text
leanprover/lean4:v4.36.0
```

Mathlib は必須ではありません。利用する場合は利用側で Lake の依存と版を固定し、
必要なビルドを実施してください。この共通 CI は Mathlib の取得・キャッシュ・ビルドを
自動化するものではありません。

`--theorem` を付けない複数ファイルのコンパイル確認もできますが、その場合は
`axiom_audit: not_requested` と記録します。専用 CI は対象名の指定を必須にしています。
複数モジュールを検査する場合は一つのエントリーポイントから import し、対象名を列挙します。

## 判定と終了コード

| 結果 | JSON `status` | 通常モード | `--required` |
| --- | --- | --- | --- |
| Lean の検査と指定した公理監査に成功 | `passed` | 0 | 0 |
| Lean/Lake 未導入、選択したツールチェーン利用不可 | `unverified` | 0 | 1 |
| 証明失敗・警告・公理監査失敗・タイムアウト・実行エラー | `failed` | 1 | 1 |
| 証明ファイル不在・設定不正 | `failed` または CLI 引数エラー | 非ゼロ | 非ゼロ |

通常モードの未検証は成功した証明を意味しません。集約する側は終了コードだけでなく
JSON の `status` を確認します。複数結果は `failed`、`unverified`、`passed` の順で集約します。

レポートの schema は `lean-verification/1`。各結果に `reason`、元の証明の SHA-256、
Lean のバージョン、実行コマンド、終了コード、標準出力・標準エラー、診断、公理一覧を記録します。
ファイル出力は一時ファイルから置換し、保存失敗も非ゼロ終了です。
元ソースのスナップショットを `--stdin` で渡すため、監査用に証明ファイルを書き換えません。
POSIX ではタイムアウトした Lake/Lean のプロセスグループを終了します。

## 未完の証明を成功扱いしない

Lean は通常 `sorry` を警告付きで受け入れるため、`-DwarningAsError=true` を付けます。
さらに JSON の警告・エラーを確認し、ソース側で警告のエラー化を解除した場合も失敗させます。
指定した各宣言に `#print axioms` を追加し、依存公理を監査します。

許可するのは標準の `propext`、`Classical.choice`、`Quot.sound` のみです。
`sorryAx`、任意の追加公理、監査出力の欠落は失敗します。
これにより、import したコンパイル済みモジュールの未完の証明も検出します。
公理を追加したい場合は、先に命題と前提をレビューしてください。

これはレビューされた Lean ソースを通常のツールチェーンで検証する仕組みです。
任意コードのサンドボックス、Lean のカーネルの独立再実装、自然言語の要求との同一性検証ではありません。
ハッシュは入力エントリーポイントのものです。依存グラフ全体の内容証明ではないため、
再現時は CI のリビジョン・toolchain・Lake の依存ロックも確認します。
実装が定理の前提を満たすかは、通常のテストとレビューで別途確認します。

## 利用側の専用 CI

`.github/workflows/reusable-lean-verify.yml` をレビュー済みの親コミットの完全な SHA で
呼び出します。下の `PARENT_COMMIT_SHA` はその SHA に置換してください。

```yaml
name: Lean verification
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  verify:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-lean-verify.yml@PARENT_COMMIT_SHA
    with:
      project-directory: formal
      proof: Conservation.lean
      theorems: |
        Example.conservation
        Example.nonnegative
```

共通ワークフローは、自分を定義したリポジトリと SHA からラッパーを取得します。
利用側にラッパーのコピーや第二の可変参照を置きません。読み取り権限だけを使い、
Elan の配布物を固定版と SHA-256 で検査してから、利用側の固定した Lean を導入します。
証明ファイル・対象名・toolchain の不備も CI 失敗です。

結果は `lean-verification` artifact の `report.json` に保存します。
セットアップに失敗して検証を開始できなかった場合も `unverified` の初期レポートを残し、
ジョブの失敗を維持します。ブランチ保護の必須チェック設定は変更しません。

親の `Lean verification` ワークフローは実際の Lean を使い、正常な証明に加えて、
偽の命題・直接の `sorry`・警告設定の解除・独自公理・import した `sorryAx`・Lake 経由を検査します。
`LEAN_VERIFY_REQUIRE=1` により、このレーンでは Lean 未導入時にテストをスキップできません。
通常の unittest/pytest では実 Lean の統合テストを未検証として明示的にスキップでき、
ラッパーの状態判定・タイムアウト・JSON 出力は Lean なしで検査します。

一次資料: [Lean の証明検証と公理](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)、
[Elan によるツールチェーン管理](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/)。
