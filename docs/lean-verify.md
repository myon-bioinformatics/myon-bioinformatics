# Lean 形式検証

通常実行は Python 標準ライブラリのみ。Lean は専用 CI レーンで使用する。

```bash
python -S lean_verify.py proofs/Example.lean
python -S lean_verify.py --required --lake proofs/Example.lean
```

JSON schema: lean-verification/1。passed / failed / unverified を区別する。
--required は未導入も失敗にする。Lake はプロジェクトルートで実行する。
警告をエラー扱いにし、sorry の警告を見逃さない。ただし任意の公理や証明の妥当な仕様との対応は別途レビューする。
各利用側で lean-toolchain を固定し、必要な場合のみ Mathlib を使用する。
Python の回帰テスト・xprobe の反例探索・Lean の形式証明は別の証跡として保持する。
証明の成功は Python 実装の正しさを直接保証しない。
通常テストはモックでラッパーを検証し、専用レーンは実 Lean を実行する。
