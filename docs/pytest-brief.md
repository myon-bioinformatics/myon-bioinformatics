# `pytest_brief.py`

`pytest_brief.py` は、必要最小限の依存関係インストールと pytest 実行結果の重要サマリー（失敗情報、カバレッジ、統計）のみを出力する標準ライブラリのみで構成された親ツールです（`browser-test-kit` より移植）。

他リポジトリ（consumer）は本リポジトリの `pytest_brief.py` を vendor して利用します。

## 主な機能と特徴

1. **依存関係の差分解析と最小限インストール**
   - `--requirements`（既定: `tests/requirements.txt` が存在すればそのパス）に指定された依存関係を解析します。
   - ターゲット Python の `pip`（>= 23.0）の `--dry-run --report` 機能を使用して、未インストールのパッケージのみを特定し、無駄な再インストールを防止します。
   - PEP 668 の `EXTERNALLY-MANAGED` 環境（OS パッケージ管理下の Python など）を自動検出し、必要に応じて `--break-system-packages` を付与してインストールを実行します（venv 内では自動的に除外されます）。

2. **pytest 実行とサマリーの抽出**
   - `python -m pytest -q -rfE -p no:cacheprovider` として pytest を実行します。
   - テスト失敗（`FAILED`, `ERROR`）、カバレッジ率（`TOTAL ...`, `Required test coverage ...`）、最終サマリーライン（`X passed, Y failed in ...` など）のみをフィルタリングして標準出力に印刷します。
   - 引数指定エラー等で pytest 自体が起動・正常終了しなかった場合（終了コード 2〜5 でサマリーがない場合）は、末尾 `--tail` 行（既定 15 行）を出力します。

3. **標準ライブラリのみで動作**
   - `python -S pytest_brief.py` として起動可能であり、実行対象の pytest や pip と疎結合に動作します。

## 使い方・CLI 引数

`pytest_brief.py` への引数と、`pytest` 自身に引き渡す引数は `--` で分離します。

```sh
python -S pytest_brief.py [pytest_brief_options] -- [pytest_options]
```

### 例

```sh
# 基本的な使用例
python -S pytest_brief.py -- --cov=xprobe --cov-branch --cov-report=term --cov-fail-under=90

# 別ファイルの requirements を指定してテスト実行
python -S pytest_brief.py --requirements tool/python/requirements.txt -- tool/python/tests
```

### オプション一覧

- `--requirements PATH`: 依存関係ファイル（既定: `tests/requirements.txt` が存在すれば使用）
- `--tail N`: pytest が起動しなかった場合に表示するログの行数（既定: 15）
- `--python PATH`: pip および pytest を実行する Python インタプリタのパス（既定: `sys.executable`）

## 終了コード

- `0`: 全テスト成功（またはスキップ）
- `1`: テスト失敗
- `3`: 依存関係の解析またはインストールに失敗
- その他: `pytest` 独自の終了コードをそのまま返却します。
