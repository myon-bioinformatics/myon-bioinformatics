# Lean 導入先と実装 PR の索引

共通ラッパーを使う **12 リポジトリ・65 公開定理**の索引です。
実装 PR と検証対象をたどるための記録であり、マージ状況やブランチ保護の状態を示すものではありません。
各リポジトリの役割を保ち、固有の命題と Python の接続テストを追加します。

## 共通基盤

- 親の実装 PR: [myon-bioinformatics #401](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/401)。
- 共通ラッパー: `lean_verify.py`。専用 CI: `.github/workflows/reusable-lean-verify.yml`。
- 利用側が参照する親の固定 SHA: `6c2558c4251698c50bc07bad86fb49f902b2738f`。
- 各 `formal/lean-toolchain`: `leanprover/lean4:v4.36.0`。今回の証明は Lean 付属の `Std` を使い、Mathlib は不要。
- 各 caller は `.github/workflows/lean-verification.yml` に、証明ファイルと名前空間付きの監査対象名を列挙する。

通常実行・通常の Python テストに Lean 依存を追加しません。Lean 未導入は `unverified` と記録し、
`--required` を使う専用 CI では未検証・証明失敗・公理監査失敗を失敗として扱います。
起動方法、JSON の判定、公理の監査条件は [Lean 形式検証](lean-verify.md) を参照してください。

## 導入一覧

パスは各リポジトリのルートからの相対パスです。対象のリビジョンと変更内容は実装 PR で確認します。
定理数は caller が明示的に監査する公開定理の数で、private な補題と親の検証用 fixture は含みません。

| リポジトリ | 実装 PR | 証明ファイル | 公開定理 | Python 接続テスト |
| --- | --- | --- | ---: | --- |
| FluidFullSpeedSimulation | [#64](https://github.com/myon-bioinformatics/FluidFullSpeedSimulation/pull/64) | `formal/Conservation.lean` | 5 | `tests/test_formal_bridge.py` |
| TalkCompress | [#220](https://github.com/myon-bioinformatics/TalkCompress/pull/220) | `formal/Budget.lean` | 6 | `tests/test_formal_budget_bridge.py` |
| PythDRagoras | [#76](https://github.com/myon-bioinformatics/PythDRagoras/pull/76) | `formal/RuleSoundness.lean` | 7 | `tests/test_formal_bridge.py` |
| Aoi | [#276](https://github.com/myon-bioinformatics/Aoi/pull/276) | `formal/RequestSelection.lean` | 6 | `tests/test_formal_request_selection.py` |
| xprobe | [#81](https://github.com/myon-bioinformatics/xprobe/pull/81) | `formal/Coverage.lean` | 3 | `tests/test_formal_coverage_bridge.py` |
| OnTautoLogy | [#75](https://github.com/myon-bioinformatics/OnTautoLogy/pull/75) | `formal/TruthTableSoundness.lean` | 8 | `tests/test_formal_bridge.py` |
| ONTO | [#32](https://github.com/myon-bioinformatics/ONTO/pull/32) | `formal/GraphProjection.lean` | 6 | `tests/test_formal_graph_projection.py` |
| TopOntoLogy | [#36](https://github.com/myon-bioinformatics/TopOntoLogy/pull/36) | `formal/PropertyWitnesses.lean` | 7 | `tests/test_formal_bridge.py` |
| CaChereCyCle | [#15](https://github.com/myon-bioinformatics/CaChereCyCle/pull/15) | `formal/Ownership.lean` | 2 | `tests/test_formal_ownership_bridge.py` |
| BrowserTestKit | [#207](https://github.com/myon-bioinformatics/BrowserTestKit/pull/207) | `formal/EvidenceReduction.lean` | 6 | `tests/stdlib/test_formal_evidence_reduction.py` |
| markdown | [#182](https://github.com/myon-bioinformatics/markdown/pull/182) | `formal/FenceRoundtrip.lean` | 7 | `tests/test_formal_bridge.py` |
| DRAgoWing | [#90](https://github.com/myon-bioinformatics/DRAgoWing/pull/90) | `formal/OverlapPartition.lean` | 2 | `tests/test_formal_overlap_partition.py` |
| **合計** | **12 利用側 PR** | **12 証明ファイル** | **65** | |

## 証明の範囲と境界

| リポジトリ | 証明するモデルの性質 | この証明だけでは保証しないこと |
| --- | --- | --- |
| FluidFullSpeedSimulation | Q48 の量を整数として扱う移送・遅延キューの保存則と、上限条件下の非負性 | 物理モデル、浮動小数点変換、全入力での配分アルゴリズム |
| TalkCompress | 候補数の上限、評価済み候補の最短選択、結果の文字数が増えないこと | 意味保持、実 tokenizer の予算、必ず目標文字数へ到達すること |
| PythDRagoras | AND・NOT・正の OR 節の充足性、含意の肯定判定、結合と排他性 | 一般の DNF、三値論理、実データで割当が実現できること |
| Aoi | 登録・実行可能な候補の選択、停止・終端状態の除外、選択順序 | 状態の読み込みや更新全体、実行結果の品質 |
| xprobe | 選択済みマスクの正確なペア被覆と、追加候補の新規被覆 | 貪欲選択の大域最適性、全候補の完全被覆、消費側の正しさ |
| OnTautoLogy | 有限個の Boolean 特徴の全割当、等価性、反例、矛盾した前提と含意の区別 | 自然言語の同義性、一階論理の一般妥当性、Python 構文解析 |
| ONTO | N3 token の statement から JSON グラフへの投影、端点・述語の保持、集合比較の対応 | RDF/N3 パーサー、DB 動作、RDF の意味論的同値、任意の JSON→TTL 変換 |
| TopOntoLogy | 関係の反例の健全性、分類の和集合の対称性、自己ループを補った関係の推移性 | 反例探索全体、通常の推移性との無条件の同一視、分類の意味の妥当性 |
| CaChereCyCle | 旧 manifest 内の不要な削除可能名だけを削除し、他を保持。所有名を要求名へ更新した後、同じ要求での削除段階は無変更 | manifest の真正性、書き込み段階の alias、並行 I/O、原子的な更新 |
| BrowserTestKit | 1 実行分の phase 集約、失敗優先、欠落を成功にしないこと、集計の二重加算防止 | 実行 identity のグループ化、画像内容、ブラウザ実行や観測の真実性 |
| markdown | 適応フェンスが本文で早閉じせず、行単位の本文と後続を保持すること | info string の解析、LF 以外の改行のバイト保持、HTML の安全性全体 |
| DRAgoWing | membership signature による領域分割と、各入力行の一度だけの所属 | membership の算出全体、幾何配置、Plotly の描画結果 |

詳細な仮定と実行方法は各リポジトリの `docs/formal-verification.md`、
Aoi は `formal/README.md` に記載します。

Lean が証明するのは明示したモデルの命題です。表の Python テストは実装との接続を有限の入力で検査し、
既存の回帰テスト・数値実験・反例探索と併用します。Lean 成功と Python テスト成功は別々の証跡です。
ONTO の RDF I/O 接続には既存の optional `exchange` 依存を使う部分があり、未導入時の skip を
RDF 往復の成功とは数えません。

## 検証から派生した修正

後続の runtime 修正は、それぞれの実装 PR と回帰テストで検証します。

| リポジトリ | 修正 PR | 修正対象 |
| --- | --- | --- |
| ONTO | [#33](https://github.com/myon-bioinformatics/ONTO/pull/33) | RDF literal の lexical form の保持 |
| CaChereCyCle | [#16](https://github.com/myon-bioinformatics/CaChereCyCle/pull/16) | 出力先の leaf link を介する書き込みの分離 |
| markdown | [#183](https://github.com/myon-bioinformatics/markdown/pull/183) | code block の info string とフェンスの境界 |
| BrowserTestKit | [#208](https://github.com/myon-bioinformatics/BrowserTestKit/pull/208) | 実行 identity の区別と summary 判定条件 |

これらの修正だけで、この索引の証明範囲が広がることはありません。対象定理を変更・追加するときは、
証明ファイル、caller の監査名、接続テスト、この表の件数を合わせて確認します。
