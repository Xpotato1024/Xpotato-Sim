---
status: historical
owner: implementation
last_verified: 2026-09-26
related:
  - docs/contracts/coordinated-arm-runtime.md
---

# 共同arm実行と出力監督の実装記録

Issue #571。基点は `fee0f4d47148772172c826716adc173ddaa70cf7`（PR #570 merge）。
現在仕様は [共同実行契約](../../contracts/coordinated-arm-runtime.md) を参照する。

## 実装と確認の範囲

左右を同じsnapshotで解き、作業dataを全側検査して一回で公開する運動学診断を追加。
既存Source/Mapping平面制御/DLS/limits/assemblyを再利用し、診断metadataからcommandを生成しない。
全体latch、受信時計、新epoch・fresh neutralでの再開を検証対象とする。

OSC側は既存physical sessionのprepare/dispatchを分け、全側の事前確認、部分失敗、応答timeout、
異常返信、停止中の競合、全側のlocal失効と停止requester試行を統合する。送信はin-memoryだけ。
local send receipt・router処理相関・物理停止確認を分け、最後のものは認定しない。

## 検出した問題と修正

- runtimeとschemaの責務一覧に新ownerがなかった。定義済みの配置を明示追加し、正確な集合検査を維持。
- simulation時間とhost要求時刻を混ぜないよう、共同commandをhost時刻に結び直した。
- 出力の統合fixtureは既存synthetic envelopeの[-1,1] radに対してhomeのelbowが範囲外だった。
  実装/限界を緩和せず、positive接続試験の初期状態を明示的にその内側へ設定。負のgate試験を別に保持。
- 統合fixtureのclockを、既存helperの正しい返却要素から取得し、adapterの最終guardと共同runtimeで同じhost時刻へ揃えた。
- local許可失効だけを実機stopとしない。receiver固有停止は必須の外部接続点であり、試行結果を各側に残す。

## 検証記録と限界

最終の対象SHA、focused/full/architecture/strict Markdown/compile、CLI出力、CIはPR本文へ記録する。
既存project環境を使用し、原本mesh/XML、安全制限、依存関係、CI workflowは変更しない。
独立した別AIレビュー、人のGamepad操作、ブラウザ双腕表示、serial/OSC送信、実機は未実施。
この記録は通常のsoftware testであり、研究参加者実験や接触モデルの妥当性検証として計上しない。
