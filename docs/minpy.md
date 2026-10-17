# minpy（単一ファイル Python バンドラ）

`tools/minpy.py` は、リポジトリ内の入口ファイルと、そこから静的に辿れる同一リポジトリの Python モジュールを、1 つの `.py` にまとめます。標準ライブラリだけで動き、親リポジトリの `tools/minpy.py` をファイル名固定で取得します。生成物は、いまのリポジトリ名ファイル（例: `xprobe.py`）と同じく raw URL から取得して vendor できます。

依存の解決や PyPI からの取得はしません。

## 依存の方針

- 標準ライブラリはそのまま外に残します（バンドルしません）。
- リポジトリ内の通常のパッケージ（`__init__.py` があるもの）とモジュールは取り込みます。絶対 import と相対 import の両方を扱います。
- サードパーティは、明示した allowlist のトップレベル名だけで、かつ `--third-party-root` にある純 Python のソースだけを取り込みます。allowlist で取り込んだ成果物は標準ライブラリ専用ではないので、カタログの `<name>_min.py` には載せません。
- ライブラリとして必要な非標準ライブラリ（モジュール直下や、スクリプト入口以外から使う関数の中）はエラーです。同じリポジトリのモジュールがあっても、標準ライブラリだけの `<name>_min.py` は作りません。
- 文字列リテラルの `importlib.import_module("名前")` と、importlib（またはその別名）から取り込んだ `import_module("名前")` の**直接呼び出し**だけを通常の import として辿り、呼び出し先は接頭辞付きの名前に書き換えます。自分で定義した `import_module` や、別オブジェクトの `loader.import_module` は書き換えません。`importlib.import_module` を代入・引数渡し・`getattr(importlib, "import_module")` で取り出す形、および `from importlib import import_module` のあと参照だけする形（`g = import_module` など）は拒否します。`from importlib import *`、`from pkgutil import *`、`from importlib.resources import *`、`from pkg_resources import *` も拒否します。引数が変数の `import_module(name)` はプラグイン読み込みとして残し、バンドル内モジュールは元の名前では読みません。finder は自分の接頭辞の名前にしか答えません。
- `__import__`、`exec`、`eval`、`spec_from_file_location` は拒否します。
- `__file__` は、`if __name__ == "__main__":` の直下だけ許します。それ以外はデータファイル同梱とみなして拒否します。`importlib.resources`、`importlib.import_module`（上記の直接呼び出し以外）、`pkgutil.get_data`、`pkg_resources` への import・属性アクセス・呼び出し（別名や `getattr(importlib, "resources")` / `getattr(importlib, "import_module")` の文字列リテラル形を含む）も拒否します。`importlib`、`pkgutil`、`pkg_resources`、`importlib.resources` のモジュールオブジェクトを属性を取らずに参照する形（代入・引数・リスト要素など）も拒否します。`importlib.metadata` や `importlib.util`、ローカル変数名の `resources` / `files` / `get_data` / `import_module`、無関係なオブジェクトの `import_module` 属性は拒否しません。変数に束ねたあとの `getattr(module, name)` や、実行時に組み立てた属性名は静的解析では拒否できない場合があります。
- スクリプト入口（その `if` と、そこからしか呼ばれない関数）の中だけにある外部 import は、min ファイルでは `ImportError` に置き換えます。メッセージは、元のファイルと伴走ツールを使うよう案内します。import しただけでは走りません。`__all__` に載っている名前、または中身を静的に読めない `__all__` があるモジュールの関数は、ライブラリ側として扱います。カタログでは、本当にスクリプト専用のときだけ元ファイルを `requires_companion_tool` で skip し、`<name>_min.py` を別エントリにします。
- 子モジュールから親パッケージへの import（`from . import helpers` や `from pkg import helpers`）は、パッケージの初期化に伴う依存なので循環には数えません。兄弟どうしの循環は既定で失敗します。親への参照を循環に数えないため、`__init__` が終わる前に親の名前を使う不具合は検出しません。その失敗は元のコードと同じく実行時に起きます。
- 次も壊れたファイルを出さず、終了コード 1 で止まります。
  - C 拡張（`.so` / `.pyd` / `.dylib`）
  - 名前空間パッケージ（`__init__.py` がないディレクトリ）
  - 循環 import（既定）。CPython と同じ部分初期化で安全なときだけ `--allow-cycles`
- `try/except ImportError` の中にあり、リポジトリ内でも allowlist でもない import は、実行時の失敗に任せます（任意依存）。それだけでは skip にしません。
- `TYPE_CHECKING` の中の import は実行されないものとして辿りません。

入口ファイルの `if __name__ == "__main__"` は、バンドルをスクリプトとして実行したときだけ動きます。import したときは動きません。スクリプト実行時の入口 `__file__` は、生成したファイル自身のパスです。

成果物の名前は `<入口の stem>_min.py` です。`probe.py` なら `probe_min.py` です。`--output` を省略すると、カレントディレクトリにその名前で書きます。

## 使い方

コミット SHA は常に引数で渡します。コードに SHA 定数はありません。`.git` があるときは HEAD がその SHA と一致する必要があり、未コミットの変更は `--require-clean` で拒否できます。`.git` が無い合成ツリーは `--skip-head-check` を付けないと失敗します。生成されるバイト列は Python のマイナー版ごとに変わります。作る側は、バンドルを生成する Python の版を固定してください。標準ライブラリの集合はその版に依存します。ヘッダの `python` とそのインタプリタが違う再生成は失敗します。

```sh
python tools/minpy.py bundle \
  --root /path/to/source \
  --entry probe.py \
  --repository example/probe \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skip-head-check \
  --output probe_min.py
```

`--output` を省略すると、入口の stem から `<stem>_min.py`（この例では `probe_min.py`）をカレントディレクトリに書きます。非標準ライブラリをライブラリ本体が import している場合はファイルを書かず、終了コード 1 です。カタログ用の skip エントリだけ欲しいときは `catalog` を使います。

```sh
python tools/minpy.py catalog \
  --root /path/to/source \
  --entry drag.py \
  --repository example/drag \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skip-head-check \
  --reason "top-level third-party import"
```

検索パスの既定はリポジトリ直下（`.`）です。`src` レイアウトなら `--path src` を繰り返します。

検証は、ヘッダの content-sha256（その行の 16 進を 64 個の 0 に置き換えた UTF-8 全体の SHA-256）と、同じツール版・同じ Python メジャー.マイナーでの再生成がバイト一致することを見ます。`--root` を付けたときだけ `status` は `regenerated` で `ok` は true で、終了コードは 0 です。`--root` が無いときは自己ハッシュだけを見て、`status` は `hash-only`、`ok` は false で、終了コードは 0 以外です。レポートの JSON はどちらも標準出力に出します。CI は `tests/fixtures/minpy_widget` を、その場で生成したファイルに対して `minpy check --root` で再生成比較します。コミット済みの `<name>_min.py` とのずれは、この CI では検出しません。本物のバンドルを登録するときは、コミットされた成果物を `minpy check --root` で比べてください。この文書の変更では公開リポジトリのピンは登録しません。

```sh
python tools/minpy.py check --bundle probe_min.py --root /path/to/source
```

## vendor メタデータ

既存の `vendor-lock/1` と `vendor-consumers/1` のエントリはそのまま有効です。バンドル成果物を指すときだけ、任意の `bundle` を足します。`html-import-vendor/1` の source も同じオブジェクトを任意で持てます。無い source のピン解決は従来どおりです。

`source` / `destination` / `commit` / `blob_sha` / `sha256` は、生成済みファイルのバイト同一性です。`bundle` は再生成に必要な入口情報です。

```json
{
  "repository": "example/widget",
  "ref": "refs/heads/main",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "source": "widget_min.py",
  "destination": "vendor/widget_min.py",
  "blob_sha": "...",
  "sha256": "...",
  "bundle": {
    "schema": "minpy-bundle/1",
    "tool": "minpy",
    "tool_version": "0.1.0",
    "entry": "widget.py",
    "search_paths": ["."],
    "allow": [],
    "allow_cycles": false
  }
}
```

`vendor-consumers/1` の entry では `repository`、`source`、`destination` に同じ `bundle` を足します。commit やダイジェストは書きません。比較結果の `bundle_mismatch` は、トポロジは同じでも `bundle` が違うときです。

ロックと再生成をまとめて確認します。

```sh
python tools/minpy.py check \
  --bundle vendor/widget_min.py \
  --lock vendor.lock.json \
  --destination vendor/widget_min.py \
  --root /path/to/source
```

## カタログ

`catalog` は `vendor-catalog/1` の tool を出します。どの tool も `source` を明示します。リポジトリ名から `<repository>.py` を補う既定には依存しません。min 版は別リポジトリにせず、同じ `repository` の別 `source` にします。`source` 必須とリポジトリ名の正規化（小文字化し、`-` と `.` を `_` にする）を足す変更とも両立します。

- ライブラリ本体が非標準ライブラリを import する: 元の `source` だけを `skipped` / `requires_external_package`。`<name>_min.py` は出しません。
- 同じリポジトリのモジュールを取り込めば標準ライブラリだけで import できる: 元の `source` は `skipped`（スクリプト専用の伴走 import なら `requires_companion_tool`、それ以外の兄弟モジュールなら `requires_sibling_module`）。`<name>_min.py` は `enrolled` / `stdlib_min_bundle`。
- 最初から標準ライブラリだけの単一ファイル: 元の `source` だけ。min エントリは作りません。
- allowlist で純 Python を取り込んだバンドルは、ファイルとしては作れますが `<name>_min.py` のカタログエントリにはしません。

公開の入口ファイルのうち、BlueProbe のように同一リポジトリのモジュール（`snapshot_source`）と、スクリプト入口からしか使わない伴走 import（`queryu`）を持つ形は min 化できます。PythDRagoras のようにトップレベルの外部パッケージ（`polars`）と同一リポジトリのモジュール（`propositions`）を両方持つ形は、外部パッケージのため標準ライブラリだけの min ファイルにはできません。テストはどちらも合成フィクスチャで、公開ソースの複製ではありません。

`vendor_sync.py check` は従来どおりロックされたバイトを見ます。再生成の一致は `minpy.py check` の仕事です。このツールは成果物を import しません。

## 出力

ヘッダにはツール版、生成した Python のメジャー.マイナー、`owner/repo`、40 文字の commit、入口、allowlist、循環の有無、モジュール一覧、content-sha256 が入ります。モジュールの格納順は名前順です。同じ入力、同じツール版、同じ Python のメジャー.マイナーなら同じバイト列になります。

内部モジュール名は、リポジトリと入口と commit から作った `_minpy_` で始まる接頭辞の下に置きます。バンドルごとに別の接頭辞なので、同じプロセスに複数のバンドルを import しても混ざりません。`_minpy_` で始まる名前は予約です。finder は接頭辞付きの名前だけを返し、利用側の同名モジュールは奪いません。入口の公開名はバンドルモジュールへコピーするだけです。`probe_min.func = ...` のような差し替えは、すでに束縛された内部の関数には届きません。スクリプトとして実行したときの traceback の filename は `minpy://<module>.py` です。`__file__` は生成ファイルのパスのままです。

## 制限

- 再生成の一致は、バンドルを作ったのと同じ `minpy` 版で確認します。
- サードパーティのルートはヘッダに記録しません。check 時に同じ `--third-party-root` を渡します。
- 1 行に import と別の文が混在するものは拒否します。
- `open("data.txt")` のようなカレントディレクトリ相対の読み取りは、元のスクリプトと同じく実行時のカレントに依存します。パッケージデータの同梱はしません。
- 変数引数の `importlib.import_module(name)` は、バンドル内のモジュールを元の名前では読みません。プラグインは通常の import 経路に任せます。
- 関数の仮引数が import した名前と同じ場合、仮引数より内側の参照と import を区別できません。
