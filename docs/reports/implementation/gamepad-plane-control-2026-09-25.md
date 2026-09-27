---
status: historical
owner: implementation
last_verified: 2026-09-25
related:
  - docs/contracts/gamepad-plane-control.md
---

# 左右独立スティック平面操作の実装・検証

## 範囲

Issue #567。基点 `833c3fe3c71f33ff675c2eb4dc1e7708a70be07b`（PR #566マージ済み）。
既存片腕fast_armへ片側操作セットを適用する段階。双腕Robot、実機、タスクGUIは変更しない。
現在の使用手順・状態機械の正本は [Gamepad平面操作契約](../../contracts/gamepad-plane-control.md)。

## 確認したこと

- 新profileの左/右それぞれについて6方向入力を既存keyboard入力と比較し、要求速度、MuJoCo qpos、観測tip変位を照合した。
- mode変更時の中立待ち、反対側への非干渉、per-side norm、stateful Mappingの実行session隔離とresetを検証した。
- legacy profile、正規化、keyboard、static axis mapを維持した。新方式のface button Z加算は無効。
- 型検査、React表示decoder/markup、入力取得の中立heartbeatと再開を検証した。最終suite件数はPRのheadと合わせて記録する。

## ブラウザ経由のsoftware smoke

既存のproject環境とheadless Chromiumを使用し、実application launcher、Vite、WebSocket、
backend Mapping、MuJoCo、実React viewerを接続した。ポートはloopbackで隔離した。
Gamepad APIだけを既知の仮想axis/buttonに差し替え、ユーザーの実Gamepadやロボットへ接続していない。
初期設定はsim-gamepad-left-xyz由来で、実行時間とportだけをsmoke専用にした。

次の11段階をbackend metadataと画面DOMで観測した。
中立ready、待機中のheartbeat、XY移動、押下時中立待ち、XZ armed、XZ移動、離上時中立待ち、
XY再arming、GUI取得停止、傾いたまま取得再開して中立待ち、中立復帰後のarming。
各段階のframe/qpos/mode、送信sample、画面テキストと最終PNGをrepository外の証拠領域へ保存した。
browserのRuntime exceptionは0。ブラウザを介した右側profileの同じsmokeと、人の操作受入は未実施。
検証終了後に自分で起動したbrowserとapplicationを閉じた。実機の停止試験ではない。

## 発見して修正した問題

初回のbrowser smokeでは、mode表示前に送るsampleにprovider session IDがなくbackendが停止した。
Gamepad providerの最初の送信からIDを発行するよう修正し、再smokeを通した。
この順序を守る回帰testを追加し、別provider生成時にはIDが変わることも確認した。

中立heartbeatからsuspend/resumeした時に同じsnapshot署名が再送を妨げる問題も、追加testで検出して修正した。

初回のMuJoCo testは簡易run_onceの返却値に、public step loopでのみ付く観測metadataを期待して失敗した。
実運用と同じbounded step loopへ検査を接続し、qpos・actual tip deltaの照合を維持した。
legacy UIのobject shapeを変えないよう、新しい表示fieldは存在するmetadataに対してのみ追加した。

## 解釈の境界

上記はソフトウェアの回帰・統合検証。参加者実験、実Gamepad機種の識別・軸校正、使いやすさや学習負荷の評価ではない。
双腕モデルの同時駆動、実機安全性、接触力の評価をこれらの成功へ読み替えない。
実機/serial/OSC、既存安全限界、既存PDFや論文repositoryは変更していない。

## PR #568のCI失敗と補修（2026-09-25）

初回GitHub Actions run `36125050979` はPython job内の変更Markdown検査で停止した。
本報告にfront matterのstatusがなく、文書の配置規約に違反していた。
そのrunのPythonテスト・compileは未実行であり、ローカルpytest成功をCI成功へ読み替えない。
同じbase SHAを渡したstrict map/link検査で再現し、実装証拠の役割に合わせて
`status: historical`と既存canonical契約への参照を追加した。既存本文は保持した。
validator、workflow、テスト、入力・制御の挙動は変更していない。

再検証では、pytestとは別に次のPR差分検査を明示実行する。

```text
python scripts/repository/validate_markdown_docs.py --base-ref 833c3fe3c71f33ff675c2eb4dc1e7708a70be07b --strict-map --strict-links
```

最終commitに対応するローカル検証とGitHub Actions結果はPR本文へ記録する。
これは文書メタデータとCI確認の補修であり、研究能力・実験条件は変わらないため、
月次研究ログとexperiment noteの追加更新は行わない。
