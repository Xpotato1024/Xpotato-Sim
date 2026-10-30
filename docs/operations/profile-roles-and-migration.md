---
status: canonical
owner: runtime
last_verified: 2026-10-28
canonical_for:
  - profile role naming and saved Workbench condition migration
related:
  - docs/contracts/launch-profile.md
  - docs/contracts/workbench.md
---

# profile の役割と条件移行

`profiles/` の分類付き ID を使用する。`demo` と `experiment` は Workbench の実行モードであり、
タスク名や入力 binding 名へ混ぜない。

| 配置 | 役割 | 例 |
| --- | --- | --- |
| `profiles/tasks/` | 入力方式に依存しない robot・model・scene・Task の定義 | `tasks/research-spring` |
| `profiles/inputs/` | モデル別の入力・mapping binding | `inputs/bimanual-gamepad` |
| `profiles/diagnostics/` | 接触・モデル診断用の明示的な起動条件 | `diagnostics/contact-debug-single` |
| `profiles/examples/` | `app` 起動例と旧 viewer 入力例 | `examples/sim-gamepad` |
| `profiles/experiments/simulation-comparison/` | ソフトウェア比較用の条件 JSON | `gamepad-force-cue.json` |
| `tests/fixtures/` | 入力再生と検証に限る fixture | `selfrionette_device/fast-arm-single-device.json` |

`selfrionette-fixture` と名付けた同梱 profile / 条件の identity と校正値は合成値であり、
実機用設定ではない。実機では正規の個体 identity、校正 profile、bridge revision、
role 接続を別途取得・入力し、準備時の検証を通す。

旧 root profile 名は自動解決しない。CLI では `tasks/dynamic-cube-drop` や
`examples/sim-gamepad` のような分類付き ID を指定する。旧 Workbench 条件 JSON を保存している場合は、
元ファイルを保持したまま、未作成の別の出力先へ一度だけ変換する。

```powershell
uv run --locked python scripts/repository/convert_workbench_condition.py `
  C:/path/to/saved-old-condition.json C:/path/to/converted-condition.json
```

変換器は登録された旧 `preset_id` を新 ID へ置き換え、現行の resolver で条件全体を検証・正規化して出力する。
差分は ID 以外にも正規化された値を含みうるため、変換前後の条件を比較してから利用する。
未知の ID、モデル・scene・mapping 等の互換しない差分、既存の出力ファイルは拒否する。
元ファイルの削除・上書きや、実機 identity の生成・校正は行わない。
追跡済みの研究比較4条件は分類付き ID へ変換済みで、ソフトウェア検証用のままである。

リポジトリ外の保存済み条件や利用者の依存関係は、この checkout からは確認できない。
変換できない過去の実験を再現する場合は、当時の commit、保存条件、依存 lock と
実機校正・接続記録をそろえ、当時の環境で検証する。現行版へ黙って読み替えない。
