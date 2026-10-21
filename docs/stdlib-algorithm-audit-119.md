# stdlibアルゴリズム活用：Issue #119 第2段階

2026-10-20。製品コードと関連テストを精読した6リポジトリの限定調査。
全公開リポジトリの棚卸し完了を意味しない。関連PRの有無も確認し、既存機能を作り直さない。

## 確認したmain

| リポジトリ | 調査時のmain SHA |
|---|---|
| myon-bioinformatics | `e5e7df176f570a7a8fa74b448912d11fca12ef04` |
| browser-test-kit | `4dcc5aa6b1e376dae9b6150a4782b9c19848a6c1` |
| cli_args | `2f24639400d6ee222b06c563284925b07e6bc7bf` |
| DRAgoWing | `360afbde2b55b54f205664eb4cf1a9cc17f9564a` |
| PythDRagoras | `b35ab6ee3cc5d71eed946c7292914a0fab0749ba` |
| NagoyAction | `1181e43508c9b2a9ad4cc4ce50f5ee38032bb678` |

## 既存機能と重複

| 実装 | 精読した使い方 | 判断 |
|---|---|---|
| 親 `gh_ops.py::_lines_differ` | `SequenceMatcher.get_opcodes()`で変更行を数える。PR本文書換えの確認は文字列完全一致 | 既存の差分表示機能を維持 |
| browser-test-kit `scripts/gh_ops.py` | 同じ行差分処理を持つ親由来の配布コピー | 別の正本を作らない。ファイル全体のハッシュは親と異なり、無条件の置換はしない |
| browser-test-kit `scripts/page_text.py::compare_rendered_text` | 正規化後の行を対応付け、追加・削除・双方変更を分類。ratioは補助指標 | DOM本文比較を重複実装しない。分類名からJS/CSSという原因を断定しない |
| cli_args `cli_args.py` | `get_close_matches`によるopt-inの未知長オプション候補。自動採用しない | PR #17は完了済み。`tests/test_stdlib_functions.py`と文書の標準関数例を製品の新機能と混同しない |
| DRAgoWing `reports/build_reports.py` | `HtmlDiff.make_table`でMarkdownソース行差分をHTML化。追加・削除はパス存在で識別 | `page_text`の取得後本文比較とは役割が異なる。単一APIへ統合しない |
| PythDRagoras `rule_search.py` | `combinations`で候補生成、`bisect.insort(..., key=...)`で上位を保持。行列経路は`heapq.nsmallest`と`chain`で全件の結果オブジェクト保持を回避 | 選択順は丸めMCC・条件数・式文字列で定義済み。一律置換しない |
| PythDRagoras `littlebit_prune.py` / `littlebit_holdout.py` | `heapq.nsmallest`・`chain` / `combinations`を使用 | 調査目的だけの別ヘルパーを増やさない |
| PythDRagoras `tests/test_littlebit_holdout_drift.py` | `unified_diff`と`islice`で失敗表示を制限、`product`で合成テストを生成 | テスト補助であり、新しい製品機能として数えない |
| 親 `tools/minpy.py` | `ast`でimport・スコープを解析し、ローカル依存を探索して単一ファイル化 | 既存APIの探索キューを今回改善 |
| NagoyAction `nagoyaction.py` | TOMLに宣言された順にステップを選択・実行。依存DAGの宣言はない | `graphlib`の導入による並べ替えは現在の契約に合わない |

親の`vendor-consumers.json`には`gh_ops.py`の配布先が登録されている。
配布コピーの存在は正本の重複開発と区別し、ロック・固定SHA・ライセンスを維持する。
今回の`minpy`改善のために`gh_ops`や他のconsumerを変更する理由はない。
調査時点の親カタログ・consumer登録に`minpy`の登録はなく、新たな配布登録も行わない。

## 採用：minpy依存探索のFIFOキュー

現実の製品処理である`_collect()`は、同じ共有依存やパッケージ親を繰り返し
リストへ追加し、`pop(0)`で先頭から取り出していた。読込み自体は`seen`で1回に
制限されるが、重複した待ち要素の確保・削除とリスト要素の移動が残る。

標準ライブラリ`collections.deque`の`popleft()`と、初回登録時点の集合で置き換える。
独自の汎用キュークラスは作らず、コピーして再利用できる既存の単一ファイル
`tools/minpy.py`内の小さな`enqueue()`へ登録規則を集める。

- 初回発見順を保つ。集合は存在確認だけに使い、集合の列挙順では探索しない。
- エラーになったモジュールも再登録しない。旧実装でも`seen`へ登録してから走査する。
- 別のimport元から同じ依存への辺はすべて記録する。キューの重複排除で辺を落とさない。
- 順序・循環判定・生成物・公開API・CLI引数・スキーマ・ツール版は維持する。
- キュー操作は登録済みモジュール数Vに対してO(V)、待ち領域もO(V)。参照ごとの
  解決・AST解析・ファイル探索のコストは残る。処理全体をO(V)とは主張しない。
- 旧キューは同時待ち数Qに対し先頭削除がO(Q)。共有依存の多い入力では待ち数が
  import参照数Eまで増える。新方式はこの要素移動と重複待ちを除く。

`difflib`・`graphlib`・`ast`・`bisect`・`heapq`・`itertools`の採用自体を目的にはしない。
具体的な問題を確認できたキューに絞る。類似度を認可・ハッシュ検証・命題の意味的正解の
代用には使用しない。

## 検証

`tests/test_minpy_dependency_queue.py`は共有依存、反復import、パッケージ親の
初回発見順、依存辺、ソース保持、共有された構文エラーの診断順、循環拒否を確認する。
`python -S tools/minpy.py`と`python -S -c 'from tools.minpy import main; ...'`を
異なる`PYTHONHASHSEED`で実行し、JSON標準出力・エラー時の空の標準出力・stderr・
終了コード0/1・出力ファイルの有無・生成バイト列一致を確認する。
CLIは従来のファイル／引数入力のままで、stdinの消費を追加しない。

- 旧実装との差分検証：固定seed 119の30グラフ（循環・構文エラーを含む）で、生成バイト列またはエラー全文が一致
- 追加回帰テスト：5 passed
- 既存minpy：98 passed
- 親全体：1278 passed / 63 skipped / 30 subtests passed（Python 3.12.14）
- `python -S tests/stdlib_runtime_smoke.py`：3 tests OK
- skippedは既存のgh/Docker等の環境条件。全て実行済みという扱いはしない。
- 計測方法・生データ：[性能記録](evidence/stdlib-119/README.md)

## 残件

- 今回読んでいないリポジトリの棚卸しと、個別の具体的問題に基づく追加適用。
- 新しいconsumerへの配布は、必要性・固定SHA・配布契約を別途確認してから判断する。
- minpyの1500段の合成依存で再現した`RecursionError`は、下記の独立した修正対象。
  FIFOキューの改善で解消したものとは扱わない。
- `graphlib`によるDAG検査は候補だが、既存の複数循環診断・順序の維持が必要。
  局所計測の500段DAGでは約0.49ms対既存0.30msであり、一律置換は採用しない。
- Issue #119全体はopenを維持する。CI成功と独立レビューを確認してから、この統合PRを
  mainへ1回だけマージする。

## 追加修正：深い依存グラフの循環診断

第2段階の後、main `9951fbc23c8a4864417efad4cbff76322dcc7873`でも1500ノードの
DAGで`RecursionError`を再現した。`_cycles()`の再帰呼出しだけを、探索中のノードと
未処理の隣接先iteratorを保持する明示的なDFSフレームに置き換える。

- 根と隣接先を名前順に探索し、探索中の祖先への辺だけから循環を記録する。
- `_normalize_cycle()`、重複除去、最終ソート、公開API、CLI、生成物の形式は維持。
- 再帰上限を引き上げない。`_collect()`やconsumer、workflow、vendor pinは変更しない。
- 全単純循環の列挙や、生成物のimport再帰・AST解析の深さ制限の解消は対象外。
  既存の循環正規化の計算量も変更していない。

回帰テストは再帰上限を超えるDAG／長い循環、自己循環、非連結成分、共有先と
探索完了済みノードへの辺、複数循環の順序を確認する。3ノードの全512有向グラフを
旧DFSと比較し、seed 119の30グラフでは公開APIの生成バイト列または診断全文も比較する。
`python -S`のscript／ワンライナーを異なる`PYTHONHASHSEED`で実行し、循環の拒否と
明示許可、stdout／stderr／終了コード、生成物の一致を検証する。既存のstdlib smokeにも
深いDAGと循環を追加し、Python 3.9／3.10の実行契約を継続して確認する。

この修正だけでIssue #119全体を完了扱いにせず、未調査repoと配布の残件は維持する。
