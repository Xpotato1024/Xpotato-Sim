---
status: supporting
owner: architecture
last_verified: 2026-09-28
canonical_for: []
related:
  - docs/contracts/coordinated-arm-runtime.md
  - docs/contracts/object-scene-contact-diagnostic.md
  - docs/contracts/launch-profile.md
---

# #582: world条件・数値積分・制御の分離

## 決定

- Sceneはworld座標、重力、支持面、物体定義と固定/可動instance、接触条件を所有する。
- Executionはphysics timestep、control周期、integrator、solver、反復数、収束許容値を所有する。
- Robotは機体/actuator/limitとservoへの投影を所有する。重力や床をRobotの新モデルへ埋め込まない。
- Taskは観測と終了条件を所有し、数値計算や世界条件を上書きしない。
- 一つのnative MjModel/MjDataの全qpos/qvelを配信する。Robot subsetのaddressとscene全体layoutを区別する。

## 比較した代替案

1. 現行qpos直接更新にmj_stepを追加: commanded値とmeasured値が混在するため不採用。
2. Viewerに物体の独立physicsを追加: 第二の状態正本となるため不採用。
3. 可動物体をmocapで追従させる/固定物体を非常に重くする: joint拘束の意味を壊すため不採用。
4. 既存R7-H単一cube manifestへ複数objectを偽装: identityとforce分類を壊すため不採用。
5. 旧versionを破壊せず、明示world/実行設定を選ぶ共通model factoryとnative stateを追加: 採用。

## 実装順と互換境界

A. strictなworld/数値計算設定、可動instance、支持面。旧fixed scene/v1と旧診断条件は不変。
B. 名前付きfreejoint/Robot address、home初期状態、全scene描画。原本Robot XML/STLは変更しない。
C. 同じ共同runtimeにdynamic providerを接続。全腕のtargetを同一pre-stepから作り、全世界を一度積分。
D. native contact forceを旧evidenceと共通primitiveで取得。幾何診断のforce未評価は維持。

旧launch v1/v2/v3用の静的resourceは記録互換のため保存する。新worldはEnvironmentが完全構成し、旧sceneから
床を削って再注入する迂回は使わない。原型/左右単腕/双腕は同じfactoryで、違いはモデル宣言だけ。

## 時間・力・停止

- control_dt / physics_dtは正の整数比。非整数比を切り捨てず拒否する。UI描画intervalとは独立。
- 各substepへ同一のactuator targetを適用。元のservo gainとforce rangeは改変せず記録する。
- qpos/qvel、ctrl target、actuator force、constraint contact forceを別項目として同時刻で保存する。
- mj_step後にmj_forwardで派生量を現在qposへ整合させる。これは追加の時間積分ではない。
- simのfault/stopは全worldの時計を凍結する。制御を止めて物体だけ勝手に落とし続けない。
  凍結はsoftware診断の意味であり、実機の停止証拠ではない。
- 柔らかい数値接触を厳密な非貫通保証とは呼ばない。食い込みとsolver条件を測定する。

## 検証基準

自由落下を解析式と比較し、固定物体不動/無重力等速/非zero初期速度/回転したfreejointの角速度frame/resetを検査。
床支持ではmgとnative反力を照合。Robotは原型/左右/双腕の全構成でcommand≠measurement、全側失敗閉鎖を検査。
旧R7-H契約、旧kinematic、ViewerのRobot関節表示と全scene姿勢、未知schema/missing identity拒否を回帰検証。
正式研究の寸法freezeは#581。今回hardware/serial/OSC、ばね・搬送Taskやparticipant実験は行わない。

## 実装レビュー時の補足

worldの明示とdynamic実行を同値条件にはしない。fixed物体だけの同じworldをkinematic診断でも利用可能にし、Scene定義が時間積分方式を決めない境界を維持する。dynamic実行には明示world、可動物体にはdynamic実行を要求し、未対応組合せを黙って固定物へ変換しない。
