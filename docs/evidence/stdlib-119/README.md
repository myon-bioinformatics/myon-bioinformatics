# minpy依存探索キューの計測

2026-10-20 / Linux / CPython 3.12.14 / 共有実行環境。
比較元は親main `e5e7df176f570a7a8fa74b448912d11fca12ef04`の`_collect()`。
現行の同じASTスキャナ・解決処理に旧関数だけを読み込み、キュー変更を比較する。
`--baseline`には実行してよい信頼済みのローカルGitコミットを指定する。

合成入力はappがn個のbranchをimportし、各branchが共通のn個のleafをimportする。
モジュール数は2n+1、参照数はn+n²。探索結果の全モジュールフィールド・挿入順・辺・
診断・外部依存と、生成バンドルのバイト一致を計測前に確認する。

時間は`perf_counter`で3回測った中央値。メモリは時間計測と別に`tracemalloc`で
測ったPythonのピーク割当で、RSSではない。速度の閾値をpytestへ埋め込まない。

| 計測 | モジュール / 参照 | 旧 / 新 中央値(s) | 旧 / 新 peak(bytes) |
|---|---:|---:|---:|
| ファイルシステムを含む小規模探索 | 41 / 420 | 0.080298 / 0.077566 | 147618 / 144668 |
| 探索部分、固定ソース・解決結果 | 201 / 10100 | 0.115822 / 0.078599 | 1216361 / 1133506 |
| 探索部分、固定ソース・解決結果 | 701 / 122850 | 1.926878 / 1.163783 | 13509290 / 12501471 |

大きい局所計測では約39.6%の時間減と約7.5%のPythonピーク割当減を観測した。
`--isolate-io`はこの固定fixtureについて`_find`と`_read_source`を差し替え、
実際のAST解析・依存探索・辺の記録はそのまま実行する。製品に解決結果キャッシュを
実装したわけではなく、解決の安全性やファイル更新時の挙動をこのstubで検証したとも
扱わない。これらは通常の回帰テストで確認する。

局所の改善をCLI全体の高速化率へ一般化しない。小規模なファイル入力では別の予備計測で
約8%遅い値も出た。共有機の変動やI/O・解決処理が支配するため、小規模入力の速度向上は
実証したと扱わない。採用根拠は大きな共有依存での待ち重複と先頭要素移動の削減、
その局所の時間・メモリ改善、生成物と診断の互換性である。

## 再現

```bash
# shallow checkoutの場合は比較元のコミットを別途取得しておく。
python -S tests/benchmark_minpy_queue.py \
  --baseline e5e7df176f570a7a8fa74b448912d11fca12ef04 --sizes 20 --repeat 3
python -S tests/benchmark_minpy_queue.py \
  --baseline e5e7df176f570a7a8fa74b448912d11fca12ef04 --sizes 100 350 --repeat 3 --isolate-io
python -m pytest -q tests/test_minpy.py tests/test_minpy_dependency_queue.py
```

生データは`small.jsonl`（通常のファイル入力）、`component.jsonl`と
`component-large.jsonl`（局所計測）。予備実行の未完了な大規模ファイル入力の
計測値は含めていない。
