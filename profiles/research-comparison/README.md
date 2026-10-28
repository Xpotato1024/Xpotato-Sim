# ばね操作の最小4条件テンプレート

`g-no_force_cue.json`、`g-force_cue.json`、`s-no_force_cue.json`、`s-force_cue.json` は
Workbenchの条件JSON importで読み込む。両入力で `fast_arm/v1`、`single_original/v1`、
`compression_spring`、`contact_observation_task/v1` を共有する。既存の双腕G presetと
単腕S presetを比較同等とは扱わない。表示軸は既存の `display_comparison.mode` のみである。

GはブラウザーGamepad、Sは既存の `selfrionette_device` と `normalized_endpoint_velocity` を使う。
Sの機構・信号処理・既存Mappingを単純化しない。Sのidentityは明示software fixture由来であり、
実機測定・校正値ではない。実機利用には正規Device profileと実際のidentity、bridge revision、
operator条件、明示接続設定が必要である。fixture値を実機profileとして適用しない。
接続設定がないDeviceは準備時に理由付きで拒否され、serialを開かない。

ばね軸はworld -Y、初期変位・速度は0、範囲は0〜0.05 m、剛性は100 N/m、減衰は2 N s/m。
これらは操作可能なソフトモデルの例示値であり、実物の同定値・研究上の閾値ではない。
MuJoCoのjoint limitは数値solverによる制約であり、硬い位置clampを保証しない。
Taskは接触の観測だけであり、押圧再現・抵抗強弱判断のいずれを最終課題とするかは未決定である。
人数、学術的な成功閾値、知覚的同等性は定義しない。「Sだけ疑似反力が出る」は未証明仮説である。

デモは設定画面のモードで選び、「検証・準備」で適用する。デモでは実験記録を開始しない。
入力切替は「入力を選び直す（停止）」から、ソース選択・条件選択・準備・開始・新しい中立の順で行う。
実験中は入力と条件を固定する。実験記録の契約は
[有限試行](../../docs/contracts/finite-trial-runtime.md)、操作は
[Workbench](../../docs/contracts/workbench.md)を参照する。

新規の Workbench 操作では、入力方式に依存しない `research-spring` 共通タスクを選び、
Gamepad / Selfrionette の binding を別に選択する。Task は `profiles/task-templates/research-spring.json` に定義する。binding は既存の単一 endpoint 用
`fast-arm-single-gamepad` / `fast-arm-single-device` から入力と mapping を解決する。
このフォルダの S/G 別 JSON は旧条件の再読込用に残す。共通タスクの条件 JSON では
`preset_id` は両入力とも `research-spring` であり、計画した入力方式は
`configuration.input.provider` に、実際の入力 provenance は試行の `start.json` に記録する。
実機の校正済み identity、role 接続、neutral 確認を省略できるという意味ではない。