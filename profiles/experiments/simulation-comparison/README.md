# ソフトウェア比較用の4条件

このフォルダの条件 JSON は Workbench の JSON import で読む。共通タスク
`tasks/research-spring` の同じ robot、`single_original` model、ばね scene、
`contact_observation_task` を使用し、入力 source と明示的画面力表示の ON/OFF だけを変える。

| 条件 | 入力 | 表示 |
| --- | --- | --- |
| `gamepad-no-force-cue.json` | Gamepad | OFF |
| `gamepad-force-cue.json` | Gamepad | ON |
| `selfrionette-fixture-no-force-cue.json` | 合成 Selfrionette fixture | OFF |
| `selfrionette-fixture-force-cue.json` | 合成 Selfrionette fixture | ON |

Selfrionette の identity・校正・mapping は software fixture の例示値であり、
実機の識別値や校正値ではない。実機では正規 profile と接続設定を別途取得・入力し、
role・neutral の検証を通す。フォルダ名は実験データの取得を意味しない。
Workbench でデモを選んだ場合、実験記録は作られない。実験の閾値や知覚結果は定義しない。

[有限試行](../../../docs/contracts/finite-trial-runtime.md)、
[Workbench](../../../docs/contracts/workbench.md)、
[profile 配置と移行](../../../docs/operations/profile-roles-and-migration.md)を参照。
